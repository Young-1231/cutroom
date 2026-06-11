"""Render layer: EDL validation/snapping, ASS captions, ffmpeg renders, receipts.

Kept import-light on purpose: other modules lazy-import specific functions
(e.g. `from cutroom.render.edl import validate_edl`) without pulling ffmpeg
helpers along.
"""
