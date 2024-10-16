# Basic k9s mutating webhook handler

* Changes the terminationGracePeriodSeconds to 1 year by default

* Use `GRACE_PERIOD_SECONDS` environment variable to set a custom terminationGracePeriodSeconds
