#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import uuid
import grpc

import restaurant_pb2 as pb
import restaurant_pb2_grpc as pb_grpc

DEFAULT_ADDR = "localhost:50051"

def money_from_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100}.{cents % 100:02d}"

def input_nonempty(prompt: str) -> str:
    while True:
        s = input(prompt).strip()
        if s:
            return s
        print("Please enter a value.")

def input_optional(prompt: str) -> str:
    return input(prompt).strip()

def input_int(prompt: str, min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    while True:
        s = input(prompt).strip()
        try:
            v = int(s)
        except ValueError:
            print("Please enter an integer.")
            continue
        if min_value is not None and v < min_value:
            print(f"Please enter an integer >= {min_value}.")
            continue
        if max_value is not None and v > max_value:
            print(f"Please enter an integer <= {max_value}.")
            continue
        return v

def choose_from(prompt: str, options: List[str]) -> str:
    opt_map = {o.lower(): o for o in options}
    while True:
        s = input(prompt).strip().lower()
        if s in opt_map:
            return opt_map[s]
        print(f"Choose one of: {', '.join(options)}")

def role_name(role: pb.Role) -> str:
    try:
        return pb.Role.Name(role)
    except Exception:
        return str(role)

def status_name(status: pb.OrderStatus) -> str:
    try:
        return pb.OrderStatus.Name(status)
    except Exception:
        return str(status)

def category_name(cat: pb.MenuCategoryName) -> str:
    try:
        return pb.MenuCategoryName.Name(cat)
    except Exception:
        return str(cat)

def gen_request_id(prefix: str = "r") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

class Session:
    def __init__(self, addr: str = DEFAULT_ADDR):
        self.addr = addr
        self.token: Optional[str] = None
        self.role: pb.Role = pb.ROLE_UNSPECIFIED

    def metadata(self) -> List[Tuple[str, str]]:
        if not self.token:
            return []
        return [("authtoken", self.token)]

class RestaurantClient:
    def __init__(self, session: Session):
        self.session = session
        self.channel = grpc.insecure_channel(session.addr)

        self.auth = pb_grpc.AuthServiceStub(self.channel)
        self.menu = pb_grpc.MenuServiceStub(self.channel)
        self.order = pb_grpc.OrderServiceStub(self.channel)
        self.kitchen = pb_grpc.KitchenServiceStub(self.channel)

        self._item_by_id: Dict[str, Tuple[str, int, int]] = {}
        self._items_by_category: Dict[int, List[Tuple[str, str, int]]] = defaultdict(list)

        self._last_kitchen_id: Optional[str] = None
        self._last_active_tickets: List[pb.KitchenTicket] = []

    def login(self) -> None:
        user_id = input_nonempty("User ID: ")
        password = input_nonempty("Password: ")

        try:
            resp = self.auth.Authenticate(pb.AuthRequest(userID=user_id, password=password))
        except grpc.RpcError as e:
            print(f"[Login failed] {e.code().name}: {e.details()}")
            return

        self.session.token = resp.authToken
        self.session.role = resp.role
        print(f"Logged in as {user_id} (role={role_name(resp.role)})")

    def logout(self) -> None:
        if not self.session.token:
            print("Not logged in.")
            return
        try:
            resp = self.auth.Logout(pb.LogoutRequest(), metadata=self.session.metadata())
            ok = getattr(resp, "ok", False)
            print(f"Logout {'successful' if ok else 'failed'}")
        except grpc.RpcError as e:
            print(f"[Logout failed] {e.code().name}: {e.details()}")
            return
        finally:
            self.session.token = None
            self.session.role = pb.ROLE_UNSPECIFIED

    def refresh_menu_cache(self) -> bool:
        if not self.session.token:
            print("You must login first.")
            return False
        try:
            resp = self.menu.GetMenu(pb.MenuGetRequest(), metadata=self.session.metadata())
        except grpc.RpcError as e:
            print(f"[GetMenu failed] {e.code().name}: {e.details()}")
            return False

        self._item_by_id.clear()
        self._items_by_category.clear()

        for cat in resp.menu.categories:
            cat_enum = int(cat.name)
            for item in cat.items:
                self._item_by_id[item.itemID] = (item.name, cat_enum, int(item.priceCents))
                self._items_by_category[cat_enum].append((item.itemID, item.name, int(item.priceCents)))

        for cat_enum in list(self._items_by_category.keys()):
            self._items_by_category[cat_enum].sort(key=lambda x: x[0])

        return True

    def get_menu(self) -> None:
        if not self.refresh_menu_cache():
            return

        if not self._items_by_category:
            print("(Menu is empty)")
            return

        print("\n===== MENU =====")
        for cat_enum in [pb.STARTERS, pb.MAINS, pb.DESSERTS, pb.DRINKS]:
            items = self._items_by_category.get(int(cat_enum), [])
            if not items:
                continue
            print(f"\n[{category_name(cat_enum)}]")
            for item_id, name, price in items:
                print(f"  {item_id:>6}  {name:<30}  {money_from_cents(price)}")
        print("==============\n")

    def update_menu(self) -> None:
        if not self.session.token:
            print("You must login first.")
            return

        print("\nUpdateMenu requires MANAGER role on the server.")
        op = choose_from("Operation (ADD/UPDATE/DELETE): ", ["ADD", "UPDATE", "DELETE"])

        cat_str = choose_from(
            "Category (STARTERS/MAINS/DESSERTS/DRINKS): ",
            ["STARTERS", "MAINS", "DESSERTS", "DRINKS"],
        )
        cat_enum = getattr(pb, cat_str)

        item_id = input_nonempty("Item ID (e.g., m1): ")
        if op == "DELETE":
            name = "DELETE"
            price = 0
        else:
            name = input_nonempty("Item name: ")
            price = input_int("Price in cents (e.g., 1299): ", min_value=0)

        req = pb.MenuUpdateRequest(
            operation=op,
            category=cat_enum,
            item=pb.MenuItem(itemID=item_id, name=name, priceCents=price),
        )

        try:
            resp = self.menu.UpdateMenu(req, metadata=self.session.metadata())
        except grpc.RpcError as e:
            print(f"[UpdateMenu failed] {e.code().name}: {e.details()}")
            return

        if resp.ok:
            print("Menu updated successfully.")
            self.refresh_menu_cache()
        else:
            print(f"Menu update failed: {resp.error}")

    def _item_label(self, item_id: str) -> str:
        info = self._item_by_id.get(item_id)
        if not info:
            return item_id
        return info[0]

    def _ensure_menu_loaded(self) -> bool:
        if not self._item_by_id:
            return self.refresh_menu_cache()
        return True

    def _choose_item_from_category(self, cat_enum: int) -> str:
        items = self._items_by_category.get(int(cat_enum), [])
        if not items:
            raise RuntimeError(f"No items available in category {category_name(cat_enum)}")

        while True:
            item_id = input_nonempty(f"Enter {category_name(cat_enum)} itemID: ")
            if item_id in self._item_by_id and self._item_by_id[item_id][1] == int(cat_enum):
                return item_id
            print("Invalid itemID for this category. Try again.")

    def _choose_item_from_category_or_skip(self, cat_enum: int) -> Optional[str]:
        items = self._items_by_category.get(int(cat_enum), [])
        if not items:
            return None

        while True:
            s = input(f"Enter {category_name(cat_enum)} itemID (blank to skip): ").strip()
            if s == "":
                return None
            item_id = s
            if item_id in self._item_by_id and self._item_by_id[item_id][1] == int(cat_enum):
                return item_id
            print("Invalid itemID for this category. Try again.")

    def _combine_lines(self, lines: List[pb.OrderLine]) -> List[pb.OrderLine]:
        qty_by_id: Dict[str, int] = defaultdict(int)
        for l in lines:
            qty_by_id[l.itemID] += int(l.qty)
        return [pb.OrderLine(itemID=item_id, qty=qty) for item_id, qty in sorted(qty_by_id.items())]

    def submit_dine_in(self) -> None:
        if not self.session.token:
            print("You must login first.")
            return
        if not self._ensure_menu_loaded():
            return

        request_id = gen_request_id("dinein")

        table = input_int("Table number: ", min_value=1, max_value=1)
        guest_count = input_int("Guest count: ", min_value=1, max_value=4)

        all_lines: List[pb.OrderLine] = []

        for g in range(1, guest_count + 1):
            print(f"\n--- Guest {g}/{guest_count} ---")
            for cat in [pb.STARTERS, pb.MAINS, pb.DESSERTS, pb.DRINKS]:
                chosen = self._choose_item_from_category_or_skip(int(cat))
                if chosen:
                    all_lines.append(pb.OrderLine(itemID=chosen, qty=1))

        if not all_lines:
            print("No items selected. Order not submitted.")
            return

        combined = self._combine_lines(all_lines)

        req = pb.OrderSubmitRequest(
            type=pb.DINE_IN,
            requestId=request_id,
            dineIn=pb.DineInInfo(table=table, guestCount=guest_count),
            lines=combined,
        )

        self._submit_order(req)

    def submit_take_out(self) -> None:
        if not self.session.token:
            print("You must login first.")
            return
        if not self._ensure_menu_loaded():
            return

        request_id = gen_request_id("takeout")

        name = input_nonempty("Customer name: ")

        print("\nEnter take-out items one per line as: itemID qty")
        print("Press Enter on blank line when done.\n")

        lines: List[pb.OrderLine] = []
        total_qty = 0

        while True:
            remaining = 10 - total_qty
            if remaining <= 0:
                submit = input("Reached max of 10 items. Submit? (y/n)").strip().lower()
                if submit == "y":
                    break
                else:
                    print("Canceling order submission.")
                    return

            s = input("Item: ").strip()
            if not s:
                break

            parts = s.split()
            if len(parts) != 2:
                print("Format must be: itemID qty")
                continue

            item_id, qty_s = parts
            if item_id not in self._item_by_id:
                print("Unknown itemID. Use 'View Menu' to see valid IDs.")
                continue

            try:
                qty = int(qty_s)
                if qty <= 0:
                    raise ValueError()
            except ValueError:
                print("qty must be a positive integer")
                continue

            if total_qty + qty > 10:
                print(f"That would exceed 10 items total. You can add at most {10 - total_qty} more.")
                continue

            lines.append(pb.OrderLine(itemID=item_id, qty=qty))
            total_qty += qty

        if not lines:
            print("No items have been added. Order not submitted.")
            return

        combined = self._combine_lines(lines)

        req = pb.OrderSubmitRequest(
            type=pb.TAKE_OUT,
            requestId=request_id,
            takeOut=pb.TakeOutInfo(customerName=name),
            lines=combined,
        )

        self._submit_order(req)

    def _submit_order(self, req: pb.OrderSubmitRequest) -> None:
        try:
            resp = self.order.SubmitOrder(req, metadata=self.session.metadata())
        except grpc.RpcError as e:
            print(f"[SubmitOrder failed] {e.code().name}: {e.details()}")
            return

        self._ensure_menu_loaded()

        print(f"\nOrder submitted! orderID={resp.orderID}")
        print("Bill:")
        bill_qty: Dict[str, int] = defaultdict(int)
        bill_total: Dict[str, int] = defaultdict(int)
        for bl in resp.bill.lines:
            bill_qty[bl.itemID] += int(bl.qty)
            bill_total[bl.itemID] += int(bl.lineTotalCents)

        for item_id in sorted(bill_qty.keys()):
            name = self._item_label(item_id)
            qty = bill_qty[item_id]
            line_total = bill_total[item_id]
            print(f"  {name:<30} x{qty:<3}  {money_from_cents(line_total)}")

        print(f"Subtotal: {money_from_cents(resp.bill.subtotalCents)}\n")

    def list_orders(self) -> None:
        if not self.session.token:
            print("You must login first.")
            return
        try:
            resp = self.order.ListOrders(pb.OrderListRequest(), metadata=self.session.metadata())
        except grpc.RpcError as e:
            print(f"[ListOrders failed] {e.code().name}: {e.details()}")
            return

        if not resp.orders:
            print("(No orders)")
            return

        print("\n===== ORDERS =====")
        for o in resp.orders:
            try:
                tname = pb.OrderType.Name(o.type)
            except Exception:
                tname = str(o.type)
            print(f"{o.orderID}  type={tname:<8}  status={status_name(o.status):<16}  subtotal={money_from_cents(o.subtotalCents)}")
        print("==================\n")

    def kitchen_list_active_tickets(self) -> None:
        if not self.session.token:
            print("You must login first.")
            return
        if not self._ensure_menu_loaded():
            return

        kitchen_id = input_nonempty("Kitchen ID (e.g., kitchen): ")
        self._last_kitchen_id = kitchen_id

        try:
            resp = self.kitchen.ListActiveTickets(
                pb.KitchenListRequest(kitchenId=kitchen_id),
                metadata=self.session.metadata(),
            )
        except grpc.RpcError as e:
            print(f"[ListActiveTickets failed] {e.code().name}: {e.details()}")
            return

        tickets = list(resp.tickets)
        self._last_active_tickets = tickets
        if not tickets:
            print("(No active tickets)")
            return

        print(f"\nActive tickets: {len(tickets)}\n")
        for t in tickets:
            self._print_ticket(t)

    def kitchen_manage_active_tickets(self) -> None:
        if not self.session.token:
            print("You must login first.")
            return
        if not self._ensure_menu_loaded():
            return

        kitchen_id = self._last_kitchen_id or input_nonempty("Kitchen ID (e.g., kitchen): ")
        self._last_kitchen_id = kitchen_id

        def refresh() -> List[pb.KitchenTicket]:
            try:
                resp = self.kitchen.ListActiveTickets(
                    pb.KitchenListRequest(kitchenId=kitchen_id),
                    metadata=self.session.metadata(),
                )
            except grpc.RpcError as e:
                print(f"[ListActiveTickets failed] {e.code().name}: {e.details()}")
                return []
            tickets_local = list(resp.tickets)
            self._last_active_tickets = tickets_local
            return tickets_local

        tickets = refresh()
        if not tickets:
            print("(No active tickets)")
            return

        while True:
            if not tickets:
                print("(No active tickets)")
                return

            print("\n--- Active Tickets ---")
            for i, t in enumerate(tickets, start=1):
                print(f"{i}) {self._ticket_summary_line(t)}")
            print("r) Refresh")
            print("v) View a ticket")
            print("q) Back")

            choice = input("Select ticket # to mark READY: ").strip().lower()
            if choice in ("q", ""):
                return
            if choice == "r":
                tickets = refresh()
                continue
            if choice == "v":
                idx = input_int("Ticket # to view: ", min_value=1, max_value=len(tickets))
                self._print_ticket(tickets[idx - 1])
                continue

            try:
                idx = int(choice)
            except ValueError:
                print("Enter a ticket number, or r/v/q.")
                continue
            if idx < 1 or idx > len(tickets):
                print("Invalid ticket number.")
                continue

            t = tickets[idx - 1]
            try:
                resp = self.kitchen.NotifyOrderReady(
                    pb.OrderReadyRequest(orderId=t.orderId),
                    metadata=self.session.metadata(),
                )
            except grpc.RpcError as e:
                print(f"[NotifyOrderReady failed] {e.code().name}: {e.details()}")
                continue

            if resp.ok:
                print(f"Marked READY: orderId={t.orderId}")
                tickets.pop(idx - 1)
                self._last_active_tickets = tickets
            else:
                print("Failed")

    def _ticket_summary_line(self, t: pb.KitchenTicket) -> str:
        parts: List[str] = []
        parts.append(f"orderId={t.orderId}")
        try:
            parts.append(f"type={pb.OrderType.Name(t.orderType)}")
        except Exception:
            parts.append(f"type={t.orderType}")
        if t.orderType == pb.DINE_IN:
            parts.append(f"table={t.table}")
            parts.append(f"guests={t.guestCount}")
        elif t.orderType == pb.TAKE_OUT:
            if getattr(t, "customerName", ""):
                parts.append(f"customer={t.customerName}")

        combined: Dict[str, int] = defaultdict(int)
        for l in t.lines:
            combined[l.itemID] += int(l.qty)
        parts.append(f"items={sum(combined.values())}")
        return "  ".join(parts)

    def _print_ticket(self, t: pb.KitchenTicket) -> None:
        combined: Dict[str, int] = defaultdict(int)
        for l in t.lines:
            combined[l.itemID] += int(l.qty)

        print("===== KITCHEN TICKET =====")
        print(f"ticketId: {t.ticketId}")
        print(f"orderId : {t.orderId}")
        try:
            print(f"type    : {pb.OrderType.Name(t.orderType)}")
        except Exception:
            print(f"type    : {t.orderType}")

        if t.orderType == pb.DINE_IN:
            print(f"table   : {t.table}")
            print(f"guests  : {t.guestCount}")
        elif t.orderType == pb.TAKE_OUT:
            print(f"customer: {t.customerName}")

        print("Items:")
        for item_id in sorted(combined.keys()):
            print(f"  - {self._item_label(item_id)} x{combined[item_id]}")
        print("==========================\n")


def main() -> int:
    print("ECE 470 Restaurant Client (Interactive)")
    addr = DEFAULT_ADDR
    session = Session(addr=addr)
    client = RestaurantClient(session)

    MENU = {
        "1": ("Login", client.login),
        "2": ("Logout", client.logout),
        "3": ("View Menu", client.get_menu),
        "4": ("Update Menu (Manager Only)", client.update_menu),
        "5": ("Submit Dine-In Order", client.submit_dine_in),
        "6": ("Submit Take-Out Order", client.submit_take_out),
        "7": ("List Orders (Server/Manager)", client.list_orders),
        "8": ("Kitchen: List Active Tickets", client.kitchen_list_active_tickets),
        "9": ("Kitchen: Manage/Mark Active Tickets READY", client.kitchen_manage_active_tickets),
        "0": ("Exit", None),
    }

    while True:
        print("\n--- Main Menu ---")
        for k in sorted(MENU.keys(), key=lambda x: int(x) if x.isdigit() else 999):
            label, _func = MENU[k]
            if k == "0":
                continue
            print(f"{k}) {label}")
        print("0) Exit")

        choice = input("Select: ").strip()
        if choice == "0":
            if session.token:
                client.logout()
            break
        action = MENU.get(choice)
        if not action:
            print("Invalid selection.")
            continue
        _label, func = action
        if func:
            func()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
