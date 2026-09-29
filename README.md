# OVH SMS for Home Assistant (OVHcloud SMS notifications)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/kinsoi/ha-ovh-sms-plugin.svg)](https://github.com/kinsoi/ha-ovh-sms-plugin/releases)
[![HA version](https://img.shields.io/badge/Home%20Assistant-2024.5%2B-blue)](https://www.home-assistant.io/)

**OVH SMS** is a Home Assistant custom integration (installable with HACS) that sends **SMS notifications through the OVHcloud SMS API** (`/sms/{serviceName}/jobs`). Use it to receive text messages from your automations — alarm, intrusion, water leak, power outage, door left open — on any mobile phone, without relying on an internet messaging app.

It works with an [OVHcloud SMS account](https://www.ovhcloud.com/en/sms/) (formerly OVH Telecom SMS) in the **EU region** (`ovh-eu` API endpoint).

## Features

- **Send SMS** from automations via the standard `notify.send_message` action
- **`ovh_sms.send_sms` action** for per-message recipients, sender, priority and encoding
- **Credit sensor** showing remaining SMS credits
- **Rate limiting** with 3 strategies: drop, queue, or disabled
- **Configurable via UI** — no YAML required
- **Multiple recipients** per message
- **Custom sender ID** support
- **Clear error reporting** — failed sends show up in the automation trace, as a Home Assistant notification, and as a **Repairs** alert when your SMS credits run out
- **Multilingual UI** — English & French (setup, options, actions, errors and notifications)

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click **Integrations** → **⋮** → **Custom repositories**
3. Add `https://github.com/kinsoi/ha-ovh-sms-plugin` as an **Integration**
4. Search for **OVH SMS** and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/ovh_sms` folder into your `config/custom_components/` directory and restart Home Assistant.

## OVH API Setup

1. Go to **https://eu.api.ovh.com/createToken/**
2. Create a token with these rights:

   | Method | Path |
   |--------|------|
   | GET | `/me` |
   | GET | `/sms` |
   | GET | `/sms/*` |
   | POST | `/sms/*/jobs` |

3. Set **Validity** to **Unlimited**
4. Note the 3 keys: Application Key, Application Secret, Consumer Key
5. Find your **service name** in OVH Manager → Telecom → SMS (e.g. `sms-xx12345-1`)

A custom **sender** (alphanumeric, max 11 characters) must first be created and validated by OVHcloud in the OVH Manager (Telecom → SMS → your service → Senders). Leave the sender empty to use an OVH short number instead.

## Configuration

Go to **Settings → Devices & Services → Add Integration → OVH SMS** and follow the wizard:

1. **Credentials** — enter your API keys, service name, recipient(s) and optional sender
2. **Rate limiting** — choose a strategy (drop / queue / disabled)

If validation fails (network issue, wrong keys...), you can choose to:
- **Go back** and fix your credentials
- **Save anyway** and fix later
- **Cancel** the setup

> **YAML import** is also supported. Add `ovh_sms:` to your `configuration.yaml` — the integration will auto-import it as a config entry.

## Usage

### Find your entity ID

After setup, your notify entity ID follows this pattern:

```
notify.ovh_sms_<service_name>
```

For example, for service `sms-xx12345-1` → `notify.ovh_sms_sms_xx12345_1`

You can also find the exact ID in **Settings → Devices & Services → OVH SMS → Configure → 📖 How to use**.

### Send a notification from an automation

```yaml
action: notify.send_message
target:
  entity_id: notify.ovh_sms_sms_xx12345_1
data:
  message: "Hello from Home Assistant!"
```

`notify.send_message` only accepts `message` (and `title`, which SMS ignores) and always sends to the recipients configured in the integration.

### Send to specific recipients / advanced options

Use the `ovh_sms.send_sms` action (**OVH SMS: Send SMS** in the automation editor). Every field except `message` is optional:

```yaml
action: ovh_sms.send_sms
target:
  entity_id: notify.ovh_sms_sms_xx12345_1
data:
  message: "Alarm triggered!"
  recipients:               # defaults to the configured recipients
    - "+33612345678"
    - "+33698765432"
  sender: "MyHome"          # override default sender (max 11 chars)
  no_stop_clause: true      # false = add STOP clause
  priority: "high"          # high | medium | low | veryLow
  coding: "8bit"            # 7bit (GSM, 160 chars) | 8bit (Unicode, 70 chars)
```

Phone numbers must use the E.164 format (`+` then country code and number). Invalid input is rejected before anything is sent.

### Automation example — intrusion alert

```yaml
automation:
  - alias: "Intrusion alert"
    triggers:
      - trigger: state
        entity_id: binary_sensor.motion_living_room
        to: "on"
    conditions:
      - condition: state
        entity_id: alarm_control_panel.home
        state: "armed_away"
    actions:
      - action: notify.send_message
        target:
          entity_id: notify.ovh_sms_sms_xx12345_1
        data:
          message: "🚨 Motion detected! {{ now().strftime('%H:%M %d/%m/%Y') }}"
```

### Automation example — low credit alert

```yaml
automation:
  - alias: "Low SMS credits"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.ovh_sms_credits_sms_xx12345_1
        below: 10
    actions:
      - action: persistent_notification.create
        data:
          title: "⚠️ Low OVH SMS credits"
          message: "Only {{ states('sensor.ovh_sms_credits_sms_xx12345_1') }} credits remaining!"
```

## Options (after setup)

Go to **Settings → Devices & Services → OVH SMS → Configure**:

| Option | Description |
|--------|-------------|
| API credentials & sender | Update keys, service name, recipients or sender |
| Rate limiting | Adjust throttling strategy |
| Send a test SMS | Send a test to your configured recipients; the result is shown in the dialog |
| 📖 How to use | Usage guide with your entity ID and YAML examples |

## Rate Limiting

| Strategy | Behavior | Use case |
|----------|----------|----------|
| `drop` (default) | Excess messages are discarded | Repetitive alerts (motion, doors) |
| `queue` | Excess messages wait in queue | Critical notifications (alarm, leak) |
| `disabled` | No throttling | You manage rate limiting elsewhere |

## Errors, notifications and repairs

| Situation | What you see |
|-----------|--------------|
| An SMS cannot be sent (OVH error, no credits…) | The action fails with a clear message (visible in the automation **trace**), and a notification appears in **Notifications**. It is replaced, not stacked, and removed after the next successful SMS |
| The SMS account has no credits left | A **Settings → Repairs** alert, cleared automatically once credits are available (checked every 30 minutes, or after the next successful SMS) |
| Invalid API keys or missing token rights | The integration shows a setup error; update the keys in **Configure → API credentials** |
| OVH API unreachable at startup | Home Assistant retries the setup automatically |
| Message dropped by the rate limiter | A warning in the logs (no phone number or message content is logged) |

To troubleshoot, enable debug logs:

```yaml
logger:
  logs:
    custom_components.ovh_sms: debug
```

Debug logs include the OVH error details and the recipient numbers; do not share them publicly without removing personal data.

## YAML configuration (legacy)

```yaml
ovh_sms:
  application_key: "YOUR_AK"
  application_secret: "YOUR_AS"
  consumer_key: "YOUR_CK"
  service_name: "sms-xx12345-1"
  recipients:                       # optional: default recipients (E.164)
    - "+33612345678"
  sender: ""                        # optional: alphanumeric sender ID
  rate_limit_strategy: "drop"       # drop | queue | disabled
  rate_limit_max: 10                # max SMS per window
  rate_limit_window: 60             # window in seconds
  rate_limit_queue_size: 50         # max queued messages (queue strategy only)
```

## Development

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) and never call the real OVH API:

```bash
pip install -r requirements_test.txt
python -m pytest
```

## License

MIT
