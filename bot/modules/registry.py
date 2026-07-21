from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass
class Services:
    config: object
    db: object
    shopify: object
    claude: object


def main_menu_keyboard(modules) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(m.MENU_LABEL, callback_data=f"{m.NAME}:menu")]
            for m in modules]
    rows.append([InlineKeyboardButton("🚧 Product (coming soon)", callback_data="noop")])
    return InlineKeyboardMarkup(rows)
