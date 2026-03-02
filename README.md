# OVH SMS for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/kinsoi/ha-ovh-sms-plugin.svg)](https://github.com/kinsoi/ha-ovh-sms-plugin/releases)
[![HA version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue)](https://www.home-assistant.io/)

Send SMS notifications via the [OVHcloud SMS API](https://api.ovh.com/console/#/sms) from Home Assistant.

## Features

- **Send SMS** from automations via the `notify.send_message` action
- **Credit sensor** showing remaining SMS credits
- **Rate limiting** with 3 strategies: drop, queue, or disabled
- **Configurable via UI** — no YAML required
- **Multiple recipients** per message
- **Custom sender ID** support
- **Multilingual UI** — English & French

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

### Send to specific recipients (override defaults)

```yaml
action: notify.send_message
target:
  entity_id: notify.ovh_sms_sms_xx12345_1
data:
  message: "Hello!"
  data:
    target:
      - "+33612345678"
      - "+33698765432"
```

### Advanced options

```yaml
action: notify.send_message
target:
  entity_id: notify.ovh_sms_sms_xx12345_1
data:
  message: "Alarm triggered!"
  data:
    sender: "MyHome"          # override default sender (max 11 chars)
    no_stop_clause: true      # false = add STOP clause
    priority: "high"          # high | medium | low | veryLow
    coding: "7bit"            # 7bit (160 chars) | unicode (accents, 70 chars)
```

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
| Send a test SMS | Send a test to your configured recipients |
| 📖 How to use | Usage guide with your entity ID and YAML examples |

## Rate Limiting

| Strategy | Behavior | Use case |
|----------|----------|----------|
| `drop` (default) | Excess messages are discarded | Repetitive alerts (motion, doors) |
| `queue` | Excess messages wait in queue | Critical notifications (alarm, leak) |
| `disabled` | No throttling | You manage rate limiting elsewhere |

## YAML configuration (legacy)

```yaml
ovh_sms:
  application_key: "YOUR_AK"
  application_secret: "YOUR_AS"
  consumer_key: "YOUR_CK"
  service_name: "sms-xx12345-1"
  sender: ""                        # optional: alphanumeric sender ID
  rate_limit_strategy: "drop"       # drop | queue | disabled
  rate_limit_max: 10                # max SMS per window
  rate_limit_window: 60             # window in seconds
  rate_limit_queue_size: 50         # max queued messages (queue strategy only)
```

## License

MIT
