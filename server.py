from concurrent import futures
import uuid
from typing import Dict

import grpc

import restaurant_pb2 as pb
import restaurant_pb2_grpc as pb_grpc
import storage

TOKENS: Dict[str, int] = {}
ACTIVE_TICKETS: Dict[str, pb.KitchenTicket] = {}
ORDER_INDEX: Dict[str, pb.OrderRecord] = {}

def role_str_to_enum(role: str) -> pb.Role:
    if role == "MANAGER":
        return pb.MANAGER
    if role == "SERVER":
        return pb.SERVER
    if role == "KITCHEN":
        return pb.KITCHEN
    return pb.ROLE_UNSPECIFIED

def get_role_from_context(context: grpc.ServicerContext) -> pb.Role:
    md = dict(context.invocation_metadata())
    token = md.get("authtoken", "")
    role = TOKENS.get(token)
    if not role:
        print(f"Unauthenticated access attempt with token: {token}")
        context.abort(grpc.StatusCode.UNAUTHENTICATED, "Missing/invalid authToken")
    return role

def require_manager(context: grpc.ServicerContext) -> None:
    role = get_role_from_context(context)
    if role != pb.MANAGER:
        print(f"Unauthorized manager access attempt with role {role}")
        context.abort(grpc.StatusCode.PERMISSION_DENIED, "Manager role required")

def require_server(context: grpc.ServicerContext) -> None:
    role = get_role_from_context(context)
    if role not in (pb.MANAGER, pb.SERVER):
        print(f"Unauthorized server access attempt with role {role}")
        context.abort(grpc.StatusCode.PERMISSION_DENIED, "Server or Manager role required")

def require_kitchen(context: grpc.ServicerContext) -> None:
    role = get_role_from_context(context)
    if role != pb.KITCHEN:
        print(f"Unauthorized kitchen access attempt with role {role}")
        context.abort(grpc.StatusCode.PERMISSION_DENIED, "Kitchen role required")

class AuthService(pb_grpc.AuthServiceServicer):
    def Authenticate(self, request: pb.AuthRequest, context: grpc.ServicerContext) -> pb.AuthResponse:
        user = storage.get_user(request.userID)
        if not user or user["password"] != request.password:
            print(f"Failed login attempt for userID: {request.userID}")
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid credentials")

        token = str(uuid.uuid4())
        role_enum = role_str_to_enum(user["role"])
        TOKENS[token] = role_enum
        print(f"User {request.userID} authenticated with role {user['role']} and token {token}")
        return pb.AuthResponse(authToken=token, role=role_enum)

    def Logout(self, request: pb.LogoutRequest, context: grpc.ServicerContext) -> pb.LogoutResponse:
        md = dict(context.invocation_metadata())
        token = md.get("authtoken", "")
        TOKENS.pop(token, None)
        print(f"User logged out with token: {token}")
        return pb.LogoutResponse(ok=True)

class MenuService(pb_grpc.MenuServiceServicer):
    def GetMenu(self, request: pb.MenuGetRequest, context: grpc.ServicerContext) -> pb.MenuGetResponse:
        get_role_from_context(context)

        menu_dict = storage.get_menu_dict()
        categories = []
        for c in menu_dict.get("categories", []):
            items = [
                pb.MenuItem(itemID=i["itemID"], name=i["name"], priceCents=int(i["priceCents"]))
                for i in c.get("items", [])
            ]
            categories.append(pb.MenuCategory(name=getattr(pb, c["name"]), items=items))
        print(f"Menu retrieved for role {get_role_from_context(context)}")
        return pb.MenuGetResponse(menu=pb.Menu(categories=categories))

    def UpdateMenu(self, request: pb.MenuUpdateRequest, context: grpc.ServicerContext) -> pb.MenuUpdateResponse:
        require_manager(context)

        op = request.operation.upper().strip()
        menu = storage.get_menu_dict()

        cat_name = pb.MenuCategoryName.Name(request.category)
        cat = next((c for c in menu["categories"] if c["name"] == cat_name), None)
        if not cat:
            print(f"Menu UPDATE failed: Unknown category {cat_name}")
            return pb.MenuUpdateResponse(ok=False, error=f"Unknown category {cat_name}")

        item = {
            "itemID": request.item.itemID,
            "name": request.item.name,
            "priceCents": int(request.item.priceCents),
        }

        if op == "ADD":
            if any(i["itemID"] == item["itemID"] for i in cat["items"]):
                print(f"Menu ADD failed: Item with ID {item['itemID']} already exists in category {cat_name}")
                return pb.MenuUpdateResponse(ok=False, error="itemID already exists")
            cat["items"].append(item)

        elif op == "UPDATE":
            existing = next((i for i in cat["items"] if i["itemID"] == item["itemID"]), None)
            if not existing:
                print(f"Menu UPDATE failed: Item with ID {item['itemID']} not found in category {cat_name}")
                return pb.MenuUpdateResponse(ok=False, error="itemID not found")
            existing.update(item)

        elif op == "DELETE":
            before = len(cat["items"])
            cat["items"] = [i for i in cat["items"] if i["itemID"] != item["itemID"]]
            if len(cat["items"]) == before:
                print(f"Menu DELETE failed: Item with ID {item['itemID']} not found in category {cat_name}")
                return pb.MenuUpdateResponse(ok=False, error="itemID not found")

        else:
            print(f"Menu UPDATE failed: Invalid operation {op}")
            return pb.MenuUpdateResponse(ok=False, error="operation must be ADD/UPDATE/DELETE")

        storage.save_menu_dict(menu)
        print(f"Menu updated with operation {op} on itemID {item['itemID']} in category {cat_name} by manager")
        return pb.MenuUpdateResponse(ok=True, error="")

class OrderService(pb_grpc.OrderServiceServicer):
    def SubmitOrder(self, request: pb.OrderSubmitRequest, context: grpc.ServicerContext) -> pb.OrderSubmitResponse:
        require_server(context)

        menu = storage.get_menu_dict()
        price_by_id: Dict[str, int] = {}
        for c in menu.get("categories", []):
            for i in c.get("items", []):
                price_by_id[i["itemID"]] = int(i["priceCents"])

        bill_lines = []
        subtotal = 0
        for line in request.lines:
            if line.itemID not in price_by_id:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Unknown itemID: {line.itemID}")
            line_total = price_by_id[line.itemID] * int(line.qty)
            subtotal += line_total
            bill_lines.append(pb.BillLine(itemID=line.itemID, qty=line.qty, lineTotalCents=line_total))

        order_id = request.requestId if request.requestId else f"o_{uuid.uuid4().hex[:10]}"

        storage.append_order(
            {
                "orderID": order_id,
                "type": pb.OrderType.Name(request.type),
                "subtotalCents": subtotal,
                "lines": [
                    {"itemID": bl.itemID, "qty": bl.qty, "lineTotalCents": bl.lineTotalCents}
                    for bl in bill_lines
                ],
            }
        )

        ORDER_INDEX[order_id] = pb.OrderRecord(
            orderID=order_id,
            type=request.type,
            status=pb.SENT_TO_KITCHEN,
            subtotalCents=subtotal,
        )

        ticket = pb.KitchenTicket(
            ticketId=f"t_{uuid.uuid4().hex[:10]}",
            orderId=order_id,
            orderType=request.type,
            table=request.dineIn.table if request.type == pb.DINE_IN else 0,
            guestCount=request.dineIn.guestCount if request.type == pb.DINE_IN else 0,
            customerName=request.takeOut.customerName if request.type == pb.TAKE_OUT else "",
            lines=[pb.OrderLine(itemID=l.itemID, qty=l.qty) for l in request.lines],
            subtotalCents=subtotal,
        )
        ACTIVE_TICKETS[order_id] = ticket
        print(f"Order submitted with ID {order_id} and type {request.type}")
        return pb.OrderSubmitResponse(orderID=order_id, bill=pb.Bill(lines=bill_lines, subtotalCents=subtotal))

    def ListOrders(self, request: pb.OrderListRequest, context: grpc.ServicerContext) -> pb.OrderListResponse:
        require_server(context)
        print(f"Orders listed for role {get_role_from_context(context)}")
        return pb.OrderListResponse(orders=list(ORDER_INDEX.values()))

class KitchenService(pb_grpc.KitchenServiceServicer):
    def ListActiveTickets(self, request: pb.KitchenListRequest, context: grpc.ServicerContext) -> pb.KitchenListResponse:
        require_kitchen(context)
        print(f"Active kitchen tickets requested and sent")
        return pb.KitchenListResponse(tickets=list(ACTIVE_TICKETS.values()))

    def NotifyOrderReady(self, request: pb.OrderReadyRequest, context: grpc.ServicerContext) -> pb.OrderReadyResponse:
        require_kitchen(context)
        oid = request.orderId
        rec = ORDER_INDEX.get(oid)
        if rec:
            rec.status = pb.READY
            ORDER_INDEX[oid] = rec
        ACTIVE_TICKETS.pop(oid, None)
        print(f"Order notified as ready with ID {oid}")
        return pb.OrderReadyResponse(ok=True)

    def AckKitchenTicket(self, request: pb.KitchenAckRequest, context: grpc.ServicerContext) -> pb.KitchenAckResponse:
        require_kitchen(context)
        print(f"Kitchen ticket acknowledged with ID {request.ticketId}")
        return pb.KitchenAckResponse(ok=True)

def serve():
    server = grpc.server(thread_pool=futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_AuthServiceServicer_to_server(AuthService(), server)
    pb_grpc.add_MenuServiceServicer_to_server(MenuService(), server)
    pb_grpc.add_OrderServiceServicer_to_server(OrderService(), server)
    pb_grpc.add_KitchenServiceServicer_to_server(KitchenService(), server)

    server.add_insecure_port("[::]:50051")
    server.start()
    print("gRPC server listening on :50051")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()
