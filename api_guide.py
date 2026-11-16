"""js_api for the guide (guide.py, guide.json): what web/guide.js draws, and what it records.

guide_data  the desktop's features with their {en, zh} words, the tours, and the person's
            state: whether the first-run tour is due, and which version they last looked at
guide_mark  the tour ended ("done"), or the help list was opened (seen = this version)
"""

import json

import guide
from api_host import main


class GuideApiMixin:
    def guide_data(self) -> str:
        cfg = main.load_config()
        settings = cfg.get("settings") or {}
        data = guide.load()
        return json.dumps({
            "success": True,
            "features": guide.for_surface("desktop", settings, data),
            "tours": data.get("tours") or {},
            "state": guide.state(cfg),
        }, ensure_ascii=False)

    def guide_mark(self, tour: str = "", seen: bool = False) -> str:
        version = guide.app_version() if seen else ""
        out = {}

        def mutate(cfg):
            out["changed"] = guide.mark(cfg, tour=str(tour or ""), seen=version)
            out["state"] = guide.state(cfg)
        main.update_config(mutate)
        return json.dumps({"success": True, **out}, ensure_ascii=False)
