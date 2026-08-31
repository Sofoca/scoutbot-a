import re


class User:
    _REQUIRED = ("email", "first_name", "last_name",
                 "max_rent", "net_income", "wbs")
    _EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    _WBS_TRUE = {"yes", "ja", "true", "1"}
    _WBS_FALSE = {"no", "nein", "false", "0"}

    def __init__(self, attributes: dict):
        if not isinstance(attributes, dict):
            raise ValueError("USER_CONFIG must be a YAML mapping/dictionary")

        missing = [k for k in self._REQUIRED
                   if k not in attributes
                   or attributes[k] is None
                   or str(attributes[k]).strip() == ""]
        if missing:
            raise ValueError(f"Missing required config fields: {', '.join(missing)}")

        self.first_name = str(attributes["first_name"]).strip()
        self.last_name = str(attributes["last_name"]).strip()
        self.email = str(attributes["email"]).strip()
        self.wbs = self._parse_wbs(attributes["wbs"])
        self.kw_filter = [kw.lower() for kw in self._parse_list(attributes.get("kw_filter", []), "kw_filter")]
        self.loc_filter = [loc.lower() for loc in self._parse_list(attributes.get("loc_filter", []), "loc_filter")]
        self.max_rent = self._parse_float(attributes["max_rent"])
        self.min_rooms = self._parse_float(attributes.get("min_rooms", 0.0))
        self.max_rooms = self._parse_float(attributes.get("max_rooms"))
        self.min_sqm = self._parse_float(attributes.get("min_sqm", 0.0))
        self.net_income = self._parse_float(attributes["net_income"])

        self._validate()

    def _validate(self):
        if not self.first_name:
            raise ValueError("first_name must not be empty")
        if not self.last_name:
            raise ValueError("last_name must not be empty")
        if not self._EMAIL_RE.match(self.email):
            raise ValueError(f"email is not a valid email address: {self.email!r}")
        if self.max_rent is None or self.max_rent <= 0:
            raise ValueError("max_rent must be a positive number")
        if self.net_income is None or self.net_income <= 0:
            raise ValueError("net_income must be a positive number")
        if self.max_rooms is not None and self.max_rooms <= 0:
            raise ValueError("max_rooms must be a positive number when provided")

    @staticmethod
    def _parse_wbs(value):
        if isinstance(value, bool):
            return value
        raw = str(value).strip().lower()
        if raw in User._WBS_TRUE:
            return True
        if raw in User._WBS_FALSE:
            return False
        raise ValueError(f"wbs must be one of {sorted(User._WBS_TRUE | User._WBS_FALSE)}; got {value!r}")

    @staticmethod
    def _parse_float(value):
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace(" ", "").replace(",", ".").strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_list(value, name):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError(f"{name} must be a list of strings; got {value!r}")
