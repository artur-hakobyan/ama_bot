from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass
class Services:
    config: object
    db: object
    shopify: object
    claude: object
    keywords: object = None      # KeywordSheet, when the workbook is present
    rules: object = None         # HouseRules — style rules learned from review
    writer: object = None        # SEOWriter — three-pass long-form pipeline
    google: object = None        # GoogleClient — Sheets + Drive

    _image_cache: object = None
    _image_cache_at: float = 0.0

    def images_cached(self, ttl: int = 3600) -> list:
        """Mockup list, refreshed hourly — listing 171 files takes seconds."""
        import time
        now = time.time()
        if self._image_cache is None or now - self._image_cache_at > ttl:
            self._image_cache = self.google.list_images(
                self.config.drive_mockups_folder_id)
            self._image_cache_at = now
        return self._image_cache


def main_menu_keyboard(modules) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(m.MENU_LABEL, callback_data=f"{m.NAME}:menu")]
            for m in modules]
    rows.append([InlineKeyboardButton("🚧 Product (coming soon)", callback_data="noop")])
    return InlineKeyboardMarkup(rows)
