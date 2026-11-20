"""Billing: charging an amount, and invoices."""


def charge(amount, currency="EUR", *, retries=0):
    return amount


class Invoice:
    def __init__(self, amount):
        self.amount = amount

    def total(self, tax):
        return self.amount * (1 + tax)
