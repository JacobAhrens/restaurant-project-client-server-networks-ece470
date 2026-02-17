# client_test.py
import grpc
import restaurant_pb2 as pb
import restaurant_pb2_grpc as pb_grpc

def main():
    channel = grpc.insecure_channel("localhost:50051")

    auth = pb_grpc.AuthServiceStub(channel)
    menu = pb_grpc.MenuServiceStub(channel)
    order = pb_grpc.OrderServiceStub(channel)

    #Test Authenticate
    res = auth.Authenticate(pb.AuthRequest(userID="manager1", password="pass123"))
    token = res.authToken
    print("Authenticated role:", pb.Role.Name(res.role), "token:", token)
    md = (("authtoken", token),)

    #Test GetMenu
    m = menu.GetMenu(pb.MenuGetRequest(), metadata=md)
    print("Menu:")
    for c in m.menu.categories:
        print(f"Category: {pb.MenuCategoryName.Name(c.name)}")
        for i in c.items:
            print(f"  ItemID: {i.itemID}, Name: {i.name}, Price cents: {i.priceCents}")

    #Test UpdateMenu (ADD)
    add_resp = menu.UpdateMenu(
        pb.MenuUpdateRequest(
            operation="ADD",
            category=pb.MAINS,
            item=pb.MenuItem(itemID="m2", name="Chicken Sandwich", priceCents=1399),
        ),
        metadata=md
    )
    print("UpdateMenu ok:", add_resp.ok, "err:", add_resp.error)

    #Test GetMenu again to see the new item
    m = menu.GetMenu(pb.MenuGetRequest(), metadata=md)
    print("Updated Menu:")
    for c in m.menu.categories:
        print(f"Category: {pb.MenuCategoryName.Name(c.name)}")
        for i in c.items:
            print(f"  ItemID: {i.itemID}, Name: {i.name}, Price cents: {i.priceCents}")

    #Test SubmitOrder
    o = order.SubmitOrder(
        pb.OrderSubmitRequest(
            type=pb.TAKE_OUT,
            requestId="req1",
            takeOut=pb.TakeOutInfo(customerName="John"),
            lines=[pb.OrderLine(itemID="m1", qty=2), pb.OrderLine(itemID="d1", qty=1)],
        ),
        metadata=md
    )
    print("OrderID:", o.orderID, "Subtotal cents:", o.bill.subtotalCents)

    #Test Logout
    lo = auth.Logout(pb.LogoutRequest(), metadata=md)
    print("Logout ok:", lo.ok)

if __name__ == "__main__":
    main()
