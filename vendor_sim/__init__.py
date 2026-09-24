"""Vendor simulator: stands in for six real vendors replying to whatever RFQ was sent.

This is the *other side* of the conversation — the part a real deployment gets from real vendors.
It writes genuine files (xlsx / PDF / docx / email text / photographed rate card) wrapped in real
emails into the inbox. The application's extraction and normalisation never import this package;
they only read the files, exactly as they would read a real vendor's reply.
"""
