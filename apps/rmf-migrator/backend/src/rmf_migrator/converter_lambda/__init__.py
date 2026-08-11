"""LibreOffice converter Lambda — deployed as its own container image.

Nothing in the API or worker packages imports this: it runs in a separate
function so the only process that parses untrusted OLE2 bytes has its own
minimal role.

That separation is not egress control. A Lambda outside a VPC reaches the
internet, and LibreOffice fetches URLs a document names — measured, with the
countermeasures, in the plan's Gate 3 section. Do not describe this function as
network-isolated until Task 12 puts it in a VPC that routes nowhere.
"""
