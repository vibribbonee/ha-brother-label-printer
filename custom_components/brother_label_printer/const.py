"""Constants for Brother network label printers."""

DOMAIN = "brother_label_printer"
CONF_NAME = "name"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PRINTER_MODEL = "printer_model"
CONF_LABEL_SIZE = "label_size"
CONF_TAPE_WIDTH_MM = "tape_width_mm"
DEFAULT_PORT = 9100
DEFAULT_QL_MEDIA = "62"
DEFAULT_PT_TAPE_MM = "12"
QL_MODEL = "QL-710W"
PT_MODEL = "PT-E550W"
QL_MEDIA = (
    "12", "17x54", "17x87", "23x23", "29", "29x42", "29x90",
    "38", "39x48", "39x90", "50", "52x29", "54", "54x29",
    "60x86", "62", "62x29", "62x100",
)
PT_TAPE_WIDTHS = ("3.5", "6", "9", "12", "18", "24")
SERVICE_PRINT_LABEL = "print_label"
