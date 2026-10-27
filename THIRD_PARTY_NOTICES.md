# Third-party notices

The administrative interface follows the generic layout and interaction conventions of the locally supplied Proposal Studio source, used at the owner's request. Its original notices and proprietary distribution restrictions continue to apply to adapted source. Proposal Studio's customer data, business workflows and Git history are not included.

Runtime dependencies retain their respective licenses. Python dependencies include FastAPI (MIT), SQLAlchemy (MIT), HTTPX (BSD-3-Clause), cryptography (Apache-2.0 OR BSD-3-Clause), PyJWT (MIT), psycopg (LGPL-3.0), Uvicorn (BSD-3-Clause) and Pillow (MIT-CMU, with its bundled codec notices). Frontend dependencies include React (MIT), Vite (MIT), TypeScript (Apache-2.0), and any icon packages identified in apps/web/package.json. Docker base images/PostgreSQL/nginx retain upstream notices. Locked dependency files are the authoritative version inventories. Review the installed distribution notices before redistributing a bundled image.

The 2024 NCAcademy download is a reference only. None of its Terraform implementation or license has been relicensed into this project.

YouTube embeds and public oEmbed metadata are fetched only on explicit authorized actions under YouTube service terms. This source package contains no downloaded YouTube video, audio, captions, thumbnail or player HTML. Teachers supply transcripts and teaching notes with permission confirmation. See docs/external-media-review.md for official embedding references and coverage boundaries.

## Game Studio engine provenance

The Academy learning-game player adapts the fixed engine and strict data schema from NorthCountryEngineer/Game_Studio at source `f09860b9973b0359ea36684aad66618bf7a67bbf`. That service extracted its original game foundation from NorthCountryEngineer/Axolotl_News at `38719feef155e068ae8566fe21b3918f0809488b`. Only engine/schema code is adapted; stories, artwork, private conversations, credentials and production data are excluded. Exact source hashes and Academy runtime adaptations are recorded in `apps/web/src/game-studio-runtime/provenance.json` and `SOURCE-NOTICE.md`. Existing dependency notices and licenses remain applicable.
