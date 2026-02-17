import json
import os
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
USERS_PATH = os.path.join(DATA_DIR, "users.json")
MENU_PATH = os.path.join(DATA_DIR, "menu.json")
ORDERS_PATH = os.path.join(DATA_DIR, "orders.json")

def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)

def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    users: List[Dict[str, Any]] = _read_json(USERS_PATH)
    return next((u for u in users if u["userID"] == user_id), None)

def get_menu_dict() -> Dict[str, Any]:
    return _read_json(MENU_PATH)

def save_menu_dict(menu: Dict[str, Any]) -> None:
    _write_json(MENU_PATH, menu)

def append_order(order_record: Dict[str, Any]) -> None:
    orders: List[Dict[str, Any]] = _read_json(ORDERS_PATH)
    orders.append(order_record)
    _write_json(ORDERS_PATH, orders)
