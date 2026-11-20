from shop.billing import Invoice


def report():
    return Invoice(5).total(0.2)
