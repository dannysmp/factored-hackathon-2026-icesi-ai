"""
Tools Package
=============

How the dialogue controller invokes the tools the service exposes. ``dispatcher`` routes a tool
value to the matching ``ToolPort`` method; ``create_dispatch`` is the single, separate path to
the one tool only the controller may call. Each tool's authorization and audit live in the
``ToolPort`` implementation, not here.
"""
