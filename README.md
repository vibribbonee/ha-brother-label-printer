<img src="custom_components/brother_label_printer/brand/icon.png" alt="" width="64" height="64" align="right">

# Brother Network Label Printer

Print labels from Home Assistant dashboards and automations. Use it for pantry labels, storage bins, and reminders.

[![Add to Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=vibribbonee&repository=ha-brother-label-printer&category=integration)

## Features

- Print text or images, with controls for alignment, size, and copies.
- Send jobs over your local network.
- Tested with Brother PT-E550W tape and QL-710W roll printers.

## Install

Install from Home Assistant Marketplace and restart Home Assistant. Then go to **Settings → Devices & services → Add integration → Brother Network Label Printer**. Choose your printer model and enter its address. Keep your existing entry when upgrading.

## Print a label

In Home Assistant, open **Developer Tools → Actions**, select **Brother Network Label Printer: Print label**, and choose your printer. The action can also run in an automation.

```yaml
action: brother_label_printer.print_label
data:
  config_entry_id: YOUR_PRINTER_CONFIG_ENTRY_ID
  text: Pantry
```

[Label printing options](custom_components/brother_label_printer/services.yaml)

## Contributing

Pull requests are welcome. Describe the change and how you tested it.

[MIT license](LICENSE). OpenAI Codex assisted with development, testing, and documentation.
