"""Rendering and network transport for Brother QL and PT label printers."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont, ImageOps
from brother_ql.conversion import convert
from brother_ql.devicedependent import label_type_specs
from brother_ql.raster import BrotherQLRaster
from brother_ql.backends.helpers import send
import ptouch
from ptouch import ConnectionNetwork, Label, PTE550W

from .const import PT_MODEL, QL_MODEL


PT_TAPE_TYPES = {
    "3.5": ptouch.Tape3_5mm,
    "6": ptouch.Tape6mm,
    "9": ptouch.Tape9mm,
    "12": ptouch.Tape12mm,
    "18": ptouch.Tape18mm,
    "24": ptouch.Tape24mm,
}
QL_MAX_WIDTH_PX = 696


def _font(
    size: int, bold: bool, family: str = "sans", font_data: bytes | None = None
):
    if font_data is not None:
        try:
            return ImageFont.truetype(io.BytesIO(font_data), size=size)
        except OSError as err:
            raise ValueError(f"Could not load the selected font file: {err}") from err
    families = {
        "sans": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")
            if bold
            else ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")
        ),
        "serif": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf")
            if bold
            else ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf")
        ),
        "mono": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf")
            if bold
            else ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf")
        ),
    }
    paths = families[family]
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow before size-aware default fonts.
        return ImageFont.load_default()


def _open_image(image_data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(image_data)) as opened:
        normalized = ImageOps.exif_transpose(opened).convert("RGBA")
        background = Image.new("RGBA", normalized.size, "white")
        background.alpha_composite(normalized)
        return background.convert("RGB")


def _ql_dimensions(label_size: str) -> tuple[int, int]:
    """Return the exact printable dimensions required by brother_ql."""
    width, height = label_type_specs[label_size]["dots_printable"]
    return width, height


def _fit_ql_fixed_label(image: Image.Image, label_size: str) -> Image.Image:
    """Center an image on a fixed-size DK label without cropping it."""
    width, height = _ql_dimensions(label_size)
    if not height:
        if image.width == width:
            return image
        resized_height = max(1, round(image.height * width / image.width))
        return image.resize((width, resized_height), Image.Resampling.LANCZOS)

    fitted = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(fitted.convert("RGB"), ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return canvas


def _render_ql_text(
    text: str, label_size: str, alignment: str, text_size: str, bold: bool
) -> Image.Image:
    image_width, _ = _ql_dimensions(label_size)
    size_scale = {"small": 0.8, "normal": 1.0, "large": 1.2}[text_size]
    font = _font(max(10, round(38 * image_width / QL_MAX_WIDTH_PX * size_scale)), bold)
    line_width = image_width - max(16, round(image_width * 0.08))
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    lines = []
    for source_line in text.replace("\r", "").split("\n"):
        if not source_line:
            lines.append("")
            continue
        current = ""
        for word in source_line.split():
            chunks = []
            chunk = ""
            for character in word:
                if chunk and measure.textbbox((0, 0), chunk + character, font=font)[2] > line_width:
                    chunks.append(chunk)
                    chunk = ""
                chunk += character
            if chunk:
                chunks.append(chunk)

            for chunk in chunks:
                if current and measure.textbbox((0, 0), f"{current} {chunk}", font=font)[2] > line_width:
                    lines.append(current)
                    current = ""
                if current:
                    current = f"{current} {chunk}"
                else:
                    current = chunk
        lines.append(current)
    line_height = max(18, round(font.size * 1.3))
    image = Image.new("RGB", (image_width, max(100, len(lines) * line_height + 40)), "white")
    draw = ImageDraw.Draw(image)
    y = 20
    for line in lines:
        line_width = measure.textbbox((0, 0), line, font=font)[2]
        x = {"left": 24, "center": (image_width - line_width) // 2, "right": image_width - line_width - 24}[alignment]
        draw.text((max(0, x), y), line, fill="black", font=font)
        y += line_height
    image = _fit_ql_fixed_label(image, label_size)
    return image.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def _prepare_ql_image(image: Image.Image, label_size: str) -> Image.Image:
    """Fit images to the printable width for the selected DK media."""
    target_width, target_height = _ql_dimensions(label_size)
    if target_height:
        resized = _fit_ql_fixed_label(image, label_size)
    else:
        target_height = max(1, round(target_width * image.height / image.width))
        resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    return ImageOps.grayscale(resized).convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def _block_text_image(
    text: str,
    width: int,
    alignment: str,
    text_size: str,
    bold: bool,
    family: str,
    font_data: bytes | None = None,
) -> Image.Image:
    """Render one text block at the selected media width."""
    scale = {"small": 0.8, "normal": 1.0, "large": 1.3}[text_size]
    font = _font(max(9, round(38 * width / QL_MAX_WIDTH_PX * scale)), bold, family, font_data)
    margin = max(10, round(width * 0.04))
    max_text_width = width - margin * 2
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    lines: list[str] = []
    for source_line in text.replace("\r", "").split("\n"):
        if not source_line:
            lines.append("")
            continue
        current = ""
        for word in source_line.split():
            candidate = f"{current} {word}".strip()
            if current and measure.textbbox((0, 0), candidate, font=font)[2] > max_text_width:
                lines.append(current)
                current = word
            else:
                current = candidate
            while measure.textbbox((0, 0), current, font=font)[2] > max_text_width:
                lines.append(current[:-1])
                current = current[-1:]
        lines.append(current)

    line_height = max(16, round(font.size * 1.3))
    result = Image.new("RGB", (width, max(1, len(lines) * line_height)), "white")
    draw = ImageDraw.Draw(result)
    for index, line in enumerate(lines):
        box = measure.textbbox((0, 0), line, font=font)
        line_width = box[2] - box[0]
        x = {
            "left": margin,
            "center": (width - line_width) // 2,
            "right": width - margin - line_width,
        }[alignment]
        draw.text(
            (max(0, x - box[0]), index * line_height - box[1]),
            line,
            fill="black",
            font=font,
            stroke_width=1 if font_data is not None and bold else 0,
        )
    return result


def _render_ql_blocks(blocks: list[dict], label_size: str) -> Image.Image:
    """Compose text and image blocks vertically on a QL label canvas."""
    width, _ = _ql_dimensions(label_size)
    rendered: list[Image.Image] = []
    for block in blocks:
        kind = block["type"]
        if kind == "spacer":
            height = max(1, round(block["height_mm"] * 300 / 25.4))
            rendered.append(Image.new("RGB", (width, height), "white"))
        elif kind == "text":
            rendered.append(
                _block_text_image(
                    block["text"], width, block["alignment"], block["text_size"],
                    block["bold"], block["font"], block.get("font_data"),
                )
            )
        else:
            source = _open_image(block["image_data"])
            target_width = max(1, round(width * block["max_width_percent"] / 100))
            fitted = ImageOps.contain(source, (target_width, 2048), Image.Resampling.LANCZOS)
            row = Image.new("RGB", (width, fitted.height), "white")
            x = {
                "left": 0,
                "center": (width - fitted.width) // 2,
                "right": width - fitted.width,
            }[block["alignment"]]
            row.paste(fitted.convert("RGB"), (x, 0))
            rendered.append(row)
        spacing = round(block.get("spacing_after_mm", 0) * 300 / 25.4)
        if spacing:
            rendered.append(Image.new("RGB", (width, spacing), "white"))

    if not rendered:
        raise ValueError("At least one non-empty Brother content block is required")
    canvas = Image.new("RGB", (width, sum(image.height for image in rendered)), "white")
    y = 0
    for image in rendered:
        canvas.paste(image, (0, y))
        y += image.height
    return _prepare_ql_image(canvas, label_size)


def _render_pt_blocks(blocks: list[dict], tape_width: str) -> Image.Image:
    """Compose ordered blocks along a P-touch label's feed direction."""
    tape = PT_TAPE_TYPES[tape_width]
    height = PTE550W.PIN_CONFIGS[tape].print_pins
    tape_width_mm = float(tape_width)
    dots_per_mm = height / tape_width_mm
    max_length = 1100
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    text_blocks: list[tuple[dict, object, list[str]]] = []
    max_text_length = 0

    def wrap_lines(text: str, font) -> list[str]:
        wrapped: list[str] = []
        max_width = max_length - 20
        for source_line in text.replace("\r", "").split("\n"):
            if not source_line:
                wrapped.append("")
                continue
            current = ""
            for word in source_line.split():
                candidate = f"{current} {word}".strip()
                if current and measure.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                    current = candidate
                    continue
                if current:
                    wrapped.append(current)
                    current = ""
                chunk = ""
                for character in word:
                    if chunk and measure.textbbox((0, 0), chunk + character, font=font)[2] > max_width:
                        wrapped.append(chunk)
                        chunk = ""
                    chunk += character
                current = chunk
            wrapped.append(current)
        return wrapped

    for block in blocks:
        if block["type"] != "text":
            continue
        scale = {"small": 0.8, "normal": 1.0, "large": 1.2}[block["text_size"]]
        font_size = max(8, min(round(28 * scale), height))
        font = _font(font_size, block["bold"], block["font"], block.get("font_data"))
        lines = wrap_lines(block["text"], font)
        boxes = [measure.textbbox((0, 0), line, font=font) for line in lines]
        line_heights = [max(1, box[3] - box[1]) for box in boxes]
        while sum(line_heights) + 2 * max(0, len(lines) - 1) > height and font_size > 1:
            font_size -= 1
            font = _font(font_size, block["bold"], block["font"], block.get("font_data"))
            lines = wrap_lines(block["text"], font)
            boxes = [measure.textbbox((0, 0), line, font=font) for line in lines]
            line_heights = [max(1, box[3] - box[1]) for box in boxes]
        line_widths = [measure.textbbox((0, 0), line, font=font)[2] for line in lines]
        max_text_length = max(max_text_length, max(line_widths, default=1))
        text_blocks.append((block, font, lines))

    common_text_width = min(max_length, max(40, max_text_length + 20))
    segments: list[Image.Image] = []
    text_index = 0
    for block in blocks:
        kind = block["type"]
        if kind == "spacer":
            length = max(1, round(block["height_mm"] * dots_per_mm))
            piece = Image.new("RGB", (length, height), "white")
        elif kind == "image":
            source = _open_image(block["image_data"])
            maximum_width = max(1, round(max_length * block["max_width_percent"] / 100))
            fitted = ImageOps.contain(source, (maximum_width, height), Image.Resampling.LANCZOS)
            piece = Image.new("RGB", (fitted.width, height), "white")
            piece.paste(fitted.convert("RGB"), (0, (height - fitted.height) // 2))
        else:
            text_block, font, lines = text_blocks[text_index]
            text_index += 1
            piece = Image.new("RGB", (common_text_width, height), "white")
            draw = ImageDraw.Draw(piece)
            boxes = [measure.textbbox((0, 0), line, font=font) for line in lines]
            line_heights = [max(1, box[3] - box[1]) for box in boxes]
            y = max(0, (height - sum(line_heights) - 2 * max(0, len(lines) - 1)) // 2)
            for line, box, line_height in zip(lines, boxes, line_heights):
                line_width = box[2] - box[0]
                x = {
                    "left": 10,
                    "center": (common_text_width - line_width) // 2,
                    "right": common_text_width - line_width - 10,
                }[block["alignment"]]
                draw.text(
                    (max(0, x - box[0]), y - box[1]),
                    line,
                    fill="black",
                    font=font,
                    stroke_width=1 if text_block.get("font_data") and text_block["bold"] else 0,
                )
                y += line_height + 2
        segments.append(piece)
        spacing = round(block.get("spacing_after_mm", 0) * dots_per_mm)
        if spacing:
            segments.append(Image.new("RGB", (spacing, height), "white"))

    if not segments:
        raise ValueError("At least one non-empty Brother content block is required")
    width = sum(segment.width for segment in segments)
    if width > max_length:
        raise ValueError(f"P-touch content is too long ({width} dots; maximum is {max_length})")
    canvas = Image.new("RGB", (width, height), "white")
    x = 0
    for segment in segments:
        canvas.paste(segment, (x, 0))
        x += segment.width
    return canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def _render_pt_text(
    text: str, tape_width: str, alignment: str, text_size: str, bold: bool
) -> Image.Image:
    tape_type = PT_TAPE_TYPES[tape_width]
    printable_height = PTE550W.PIN_CONFIGS[tape_type].print_pins
    lines = text.replace("\r", "").split("\n")
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    max_length = 1100
    max_text_width = max_length - 20

    def wrap_lines(font):
        wrapped = []
        for source_line in lines:
            if not source_line:
                wrapped.append("")
                continue
            current = ""
            for word in source_line.split():
                candidate = f"{current} {word}".strip()
                if current and measure.textbbox((0, 0), candidate, font=font)[2] > max_text_width:
                    wrapped.append(current)
                    current = word
                else:
                    current = candidate
                while measure.textbbox((0, 0), current, font=font)[2] > max_text_width:
                    wrapped.append(current[:-1])
                    current = current[-1:]
            wrapped.append(current)
        return wrapped

    size_scale = {"small": 0.8, "normal": 1.0, "large": 1.2}[text_size]
    font_size = max(8, min(round(28 * size_scale), printable_height))
    font = _font(font_size, bold)
    wrapped = wrap_lines(font)
    line_boxes = [measure.textbbox((0, 0), line, font=font) for line in wrapped]
    line_heights = [max(1, box[3] - box[1]) for box in line_boxes]
    line_gap = 2
    while sum(line_heights) + line_gap * max(0, len(wrapped) - 1) > printable_height and font_size > 1:
        font_size -= 1
        font = _font(font_size, bold)
        wrapped = wrap_lines(font)
        line_boxes = [measure.textbbox((0, 0), line, font=font) for line in wrapped]
        line_heights = [max(1, box[3] - box[1]) for box in line_boxes]

    widths = [box[2] - box[0] for box in line_boxes]
    total_height = sum(line_heights) + line_gap * max(0, len(wrapped) - 1)
    y = max(0, (printable_height - total_height) // 2)
    content_width = max(widths, default=60)
    image_width = min(max_length, content_width + 20)
    image = Image.new("RGB", (image_width, printable_height), "white")
    draw = ImageDraw.Draw(image)
    for line, box, line_height in zip(wrapped, line_boxes, line_heights):
        line_width = box[2] - box[0]
        x = {"left": 10, "center": 10 + (content_width - line_width) // 2, "right": 10 + content_width - line_width}[alignment]
        draw.text((x - box[0], y - box[1]), line, fill="black", font=font)
        y += line_height + line_gap
    return image.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def _prepare_pt_image(image: Image.Image, tape_width: str) -> Image.Image:
    """Fit an image inside the printable tape height without cropping it."""
    tape_type = PT_TAPE_TYPES[tape_width]
    height = PTE550W.PIN_CONFIGS[tape_type].print_pins
    fitted = ImageOps.contain(image, (1100, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (fitted.width, height), "white")
    canvas.paste(fitted.convert("RGB"), (0, (height - fitted.height) // 2))
    return canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def print_label_job(
    host: str,
    port: int,
    model: str,
    label_size: str,
    tape_width: str,
    text: str | None,
    image_data: bytes | None,
    copies: int,
    alignment: str = "left",
    text_size: str = "normal",
    bold: bool = True,
    blocks: list[dict] | None = None,
) -> None:
    if model == PT_MODEL:
        tape = PT_TAPE_TYPES[tape_width]
        image = (
            _render_pt_blocks(blocks, tape_width)
            if blocks is not None
            else (
                _render_pt_text(text, tape_width, alignment, text_size, bold)
                if text is not None
                else _prepare_pt_image(_open_image(image_data or b""), tape_width)
            )
        )
        connection = ConnectionNetwork(host, port=port)
        try:
            printer = PTE550W(connection)
            labels = [Label(image.copy(), tape) for _ in range(copies)]
            if copies == 1:
                printer.print(labels[0], margin_mm=2.0)
            else:
                printer.print_multi(labels)
        finally:
            connection.close()
        return

    if model != QL_MODEL:
        raise ValueError(f"Unsupported Brother model: {model}")
    image = (
        _render_ql_blocks(blocks, label_size)
        if blocks is not None
        else (
            _render_ql_text(text, label_size, alignment, text_size, bold)
            if text is not None
            else _prepare_ql_image(_open_image(image_data or b""), label_size)
        )
    )
    qlr = BrotherQLRaster(QL_MODEL)
    instructions = convert(
        qlr,
        [image] * copies,
        label_size,
        cut=True,
        compress=True,
        hq=True,
        dither=False,
    )
    send(
        instructions=instructions,
        printer_identifier=f"tcp://{host}:{port}",
        backend_identifier="network",
        blocking=True,
    )
