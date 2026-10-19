import os
import yaml
import logging

logger = logging.getLogger("app")

class ConfigLoader:
    def __init__(self, config_var="USER_CONFIG"):
        self.config_var = config_var

    def load_user_data(self):
        # read in config environment variable from GitHub secrets if exists
        config_string = os.environ.get(self.config_var)
        if not config_string:
            logger.error(f"Environment variable '{self.config_var}' is not set. Please set variable as a GitHub secret and try again.")
            raise ValueError(f"Missing environment variable '{self.config_var}'")
        logger.info("Loading config from GitHub secret...")
        try:
            config = yaml.safe_load(config_string)
        except yaml.YAMLError:
            # Parser exceptions include lines from USER_CONFIG, which is a secret.
            raise ValueError("USER_CONFIG contains invalid YAML; check its formatting") from None

        if config is None:
            raise ValueError("USER_CONFIG appears to be empty or invalid YAML")
        if not isinstance(config, dict):
            raise ValueError("USER_CONFIG must be a YAML mapping/dictionary")

        return config
