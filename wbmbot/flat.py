import re
import hashlib

class Flat:
    def __init__(self, attributes: dict):
        self.detail_link = attributes.get("detail_url", "")
        self.title = attributes.get("title", "")
        self.total_rent = self._parse_currency(attributes.get("total_rent", 0.0))
        self.base_rent = self._parse_currency(attributes.get("base_rent", 0.0))
        self.size = self._parse_sqm(attributes.get("size", 0.0))
        self.rooms = self._parse_sqm(attributes.get("rooms", 0.0))
        self.zip_code = self._extract_zipcode(attributes.get("zip_code", ""))
        self.property_attrs = [attr.lower() for attr in attributes.get("property_attrs", [])]
        # WBM sometimes puts WBS only in the title (e.g. "mit WBS 140"), not the feature list.
        self._card_wbs = any(self._mentions_wbs(attr) for attr in self.property_attrs)
        self.wbs = self._card_wbs or self._mentions_wbs(self.title)
        self.available = attributes.get("available", True)
        # Compute hash for deduplication
        self.hash = self._compute_hash()

    def matches_criteria(self, user):
        if self.total_rent > user.max_rent:
            return False
        if self.rooms < user.min_rooms:
            return False
        if user.max_rooms is not None and self.rooms > user.max_rooms:
            return False
        if self.size < user.min_sqm:
            return False
        if user.wbs != self.wbs:
            return False
        if user.kw_filter and not all(
                keyword in " ".join(self.property_attrs).lower() for keyword in user.kw_filter):
            return False
        if user.loc_filter and not any(
                self.zip_code.startswith(prefix.strip()) for prefix in user.loc_filter if prefix.strip()):
            return False

        return True

    def within_range(self, user):
        # base_rent may be None when detail scrape/parse fails
        if self.base_rent is None or user.net_income is None:
            return False
        if not (0.15 * user.net_income <= self.base_rent <= 0.30 * user.net_income):
            return False
        return True

    def update_details(self, detail_attrs: dict):
        # Update or add attributes only available from detail page
        if not detail_attrs:
            return
        if "available" in detail_attrs:
            self.available = bool(detail_attrs["available"])
        if "base_rent" in detail_attrs:
            self.base_rent = self._parse_currency(detail_attrs.get("base_rent", ""))
        if "detail_url" in detail_attrs:
            self.detail_link = detail_attrs.get("detail_url", "")
        # Detail page "WBS erforderlich: Ja/Nein" is authoritative; never downgrade a WBS signal.
        if detail_attrs.get("wbs_required") is not None:
            self.wbs = self.wbs or bool(detail_attrs["wbs_required"])
        # can be extended to retrieve more attributes from details page

    def _compute_hash(self):
        # Uses the card feature-list signal only, so hashes of already-applied flats stay stable.
        hash_input = f"{self.title}{self.total_rent}{self.size}{self.rooms}{self.zip_code}{self._card_wbs}"
        return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()

    @staticmethod
    def _mentions_wbs(text):
        return bool(re.search(r"(?i)\bwbs\b|wohnberechtigungsschein", text or ""))

    @staticmethod
    def _parse_currency(value):
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            cleaned = re.sub(r"\s+", "", str(value))
            cleaned = re.sub(r"(?i)[€]|eur", "", cleaned)
            if "," in cleaned and "." in cleaned:
                if cleaned.rfind(",") > cleaned.rfind("."):
                    cleaned = cleaned.replace(".", "").replace(",", ".")
                else:
                    cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            return float(cleaned)
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _parse_sqm(value):
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            cleaned = re.sub(r"\s+", "", str(value))
            cleaned = re.sub(r"(?i)m[²2]|sqm", "", cleaned)
            if "," in cleaned and "." in cleaned:
                if cleaned.rfind(",") > cleaned.rfind("."):
                    cleaned = cleaned.replace(".", "").replace(",", ".")
                else:
                    cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            return float(cleaned)
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _extract_zipcode(text):
        match = re.search(r'\b\d{5}\b', text)
        return match.group() if match else ""