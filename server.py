from concurrent import futures
import time
import uuid
import grpc

import restaurant_pb2 as pb
import restaurant_pb2_grpc as pb_grpc
import storage

# In-memory auth token map (token -> role)
TOKENS = {}

def _role_str_to_enum(role: str) -> pb.Role:
    if role == "MANAGER":
        return pb.MANAGER
    if role == "SERVER":
        return pb.SERVER
    return pb.ROLE_UNSPECIFIED

def _require_manager(context: grpc.ServicerContext) -> None:
    md = dict(context.invocation_metadata())
    token = md.get("authtoken", "")
    role = TOKENS.get(token)
    if role != pb.MANAGER:
        context.abort(grpc.StatusCode.PERMISSION_DENIED, "Manager role required")

class AuthService(pb_grpc.AuthServiceServicer):
    def Authenticate(self, request: pb.AuthRequest, context):
        user = storage.get_user(request.userID)
        if not user or user["password"] != request.password:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid credentials")

        token = str(uuid.uuid4())
        role_enum = _role_str_to_enum(user["role"])
        TOKENS[token] = role_enum
        return pb.AuthResponse(authToken=token, role=role_enum)

    def Logout(self, request: pb.LogoutRequest, context):
        md = dict(context.invocation_metadata())
        token = md.get("authtoken", "")
        if token in TOKENS:
            del TOKENS[token]
        return pb.LogoutResponse(ok=True)

class MenuService(pb_grpc.MenuServiceServicer):
    def GetMenu(self, request: pb.MenuGetRequest, context):
        menu_dict = storage.get_menu_dict()
        categories = []
        for c in menu_dict.get("categories", []):
            items = [
                pb.MenuItem(itemID=i["itemID"], name=i["name"], priceCents=int(i["priceCents"]))
                for i in c.get("items", [])
            ]
            categories.append(pb.MenuCategory(name=getattr(pb, c["name"]), items=items))

        return pb.MenuGetResponse(menu=pb.Menu(categories=categories))

    def UpdateMenu(self, request: pb.MenuUpdateRequest, context):
        _require_manager(context)

        op = request.operation.upper().strip()
        menu = storage.get_menu_dict()

        cat_name = pb.MenuCategoryName.Name(request.category)
        cat = next((c for c in menu["categories"] if c["name"] == cat_name), None)
        if not cat:
            return pb.MenuUpdateResponse(ok=False, error=f"Unknown category {cat_name}")

        item = {"itemID": request.item.itemID, "name": request.item.name, "priceCents": int(request.item.priceCents)}

        if op == "ADD":
            if any(i["itemID"] == item["itemID"] for i in cat["items"]):
                return pb.MenuUpdateResponse(ok=False, error="itemID already exists")
            cat["items"].append(item)

        elif op == "UPDATE":
            existing = next((i for i in cat["items"] if i["itemID"] == item["itemID"]), None)
            if not existing:
                return pb.MenuUpdateResponse(ok=False, error="itemID not found")
            existing.update(item)

        elif op == "DELETE":
            before = len(cat["items"])
            cat["items"] = [i for i in cat["items"] if i["itemID"] != item["itemID"]]
            if len(cat["items"]) == before:
                return pb.MenuUpdateResponse(ok=False, error="itemID not found")

        else:
            return pb.MenuUpdateResponse(ok=False, error="operation must be ADD/UPDATE/DELETE")

        storage.save_menu_dict(menu)
        return pb.MenuUpdateResponse(ok=True, error="")

class OrderService(pb_grpc.OrderServiceServicer):
    def SubmitOrder(self, request: pb.OrderSubmitRequest, context):
        menu = storage.get_menu_dict()
        price_by_id = {}
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

        order_id = f"o_{uuid.uuid4().hex[:8]}"
        storage.append_order({
            "orderID": order_id,
            "type": pb.OrderType.Name(request.type),
            "requestId": request.requestId,
            "subtotalCents": subtotal,
            "lines": [{"itemID": bl.itemID, "qty": bl.qty, "lineTotalCents": bl.lineTotalCents} for bl in bill_lines]
        })

        return pb.OrderSubmitResponse(orderID=order_id, bill=pb.Bill(lines=bill_lines, subtotalCents=subtotal))

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_AuthServiceServicer_to_server(AuthService(), server)
    pb_grpc.add_MenuServiceServicer_to_server(MenuService(), server)
    pb_grpc.add_OrderServiceServicer_to_server(OrderService(), server)

    server.add_insecure_port("[::]:50051")
    server.start()
    print("gRPC server listening on :50051")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == "__main__":
    serve()
