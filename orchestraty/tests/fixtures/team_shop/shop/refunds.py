from shop.billing import charge


def refund(a, c):
    return charge(a, currency=c)


def flat():
    return charge(10)
