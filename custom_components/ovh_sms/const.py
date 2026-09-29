"""Constants for the OVH SMS integration."""

DOMAIN = "ovh_sms"

# Fixed endpoint
OVH_ENDPOINT = "ovh-eu"
# Seconds before an OVH API call is abandoned (python-ovh defaults to 180)
OVH_TIMEOUT = 20
# OVH service names look like "sms-ab12345-1"; they are used in API paths
SERVICE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"

# Configuration keys
CONF_APPLICATION_KEY = "application_key"
CONF_APPLICATION_SECRET = "application_secret"
CONF_CONSUMER_KEY = "consumer_key"
CONF_SERVICE_NAME = "service_name"
CONF_SENDER = "sender"
CONF_RECIPIENTS = "recipients"

# Rate limiting configuration
CONF_RATE_LIMIT_STRATEGY = "rate_limit_strategy"
CONF_RATE_LIMIT_MAX = "rate_limit_max"
CONF_RATE_LIMIT_WINDOW = "rate_limit_window"
CONF_RATE_LIMIT_QUEUE_SIZE = "rate_limit_queue_size"

# Rate limit strategies
STRATEGY_DROP = "drop"
STRATEGY_QUEUE = "queue"
STRATEGY_DISABLED = "disabled"

# Defaults
DEFAULT_SENDER = ""
DEFAULT_RATE_LIMIT_STRATEGY = STRATEGY_DROP
DEFAULT_RATE_LIMIT_MAX = 10
DEFAULT_RATE_LIMIT_WINDOW = 60  # seconds
DEFAULT_RATE_LIMIT_QUEUE_SIZE = 50

# ovh_sms.send_sms action
SERVICE_SEND_SMS = "send_sms"
ATTR_RECIPIENTS = "recipients"
ATTR_SENDER = "sender"
ATTR_NO_STOP_CLAUSE = "no_stop_clause"
ATTR_PRIORITY = "priority"
ATTR_CODING = "coding"

# Values accepted by the OVH API (sms.PriorityEnum / sms.CodingEnum)
PRIORITIES = ["high", "medium", "low", "veryLow"]
CODINGS = ["7bit", "8bit"]
