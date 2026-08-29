# Fornoff Wedding - Event Web Platform

Production wedding website built for my friends. The site is the single guest-facing channel for event information, registry discovery, and RSVP capture ahead of a response deadline.

Built as a **static multi-page front end** with a **Supabase-backed RSVP subsystem** - no custom application server, no build pipeline for content pages, and no guest login flow. Guests identify themselves by name; responses persist in PostgreSQL via the Supabase/PostgREST API.

---

## Problem & impact


| Stakeholder need                                                    | How the system addresses it                                                           |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Guests need one place for date, venue, dress code, and FAQs         | Structured content pages with semantic HTML, mobile layout, and accessible navigation |
| Couple needs headcount and dietary data for catering                | Per-guest RSVP records with attendance boolean and free-text dietary field            |
| Invitations are sent by **party** (household), not individual login | Party-centric data model; one lookup loads every guest on the invitation              |
| Common names / partial name entry                                   | Dual-index guest search plus client-side disambiguation UI                            |
| Registry spans multiple vendors                                     | Curated outbound links (Amazon, Target, Venmo) without embedding third-party widgets  |
| Couple needs response tracking before the deadline                  | Unlisted `rsvpcheck/` dashboard buckets guests by attending, declined, or no response |


The RSVP path replaces manual tracking (texts, spreadsheets) with **idempotent writes**: re-submitting updates the same row per `guest_id`, so guests can change answers without duplicate records.

---

## Repository layout

```
├── index.html              # Home: hero, story, timeline, dress code, CTAs
├── rsvp.html               # RSVP shell + progressive panels
├── faq.html                # Accessible FAQ (native <details>)
├── css/
│   ├── styles.css          # Shared design system + rsvpcheck styles
│   └── registry-list.css   # Registry hub theme
├── js/
│   ├── rsvp-app.js         # Guest lookup, party load, upsert logic
│   ├── rsvp-check.js       # Dashboard: guests vs RSVP status
│   └── supabase-config.js  # Project URL + anon key
├── registry-list/
│   └── index.html          # Registry hub (Amazon, Target, Venmo links)
├── rsvpcheck/
│   └── index.html          # Unlisted couple dashboard (not in main nav)
├── images/                 # Photography and brand assets only
├── .gitignore
└── README.md
```

Local debugging uses the parent workspace `.vscode/` launch config and task (`python -m http.server 8080`).

---

## Data model

Schema is inferred from client queries and upserts (DDL lives in Supabase, not in this repository):

```
parties
  id            uuid (PK)
  party_name    text
  phone         text | null   -- one number per household; used by the SMS blast

guests
  id            uuid (PK)
  party_id      uuid → parties.id
  first_name    text
  last_name     text | null

rsvps
  guest_id      uuid (PK / unique conflict target)
  attending     boolean
  dietary_restrictions  text
  updated_at    timestamptz
```

**Relationships:** PostgREST nested selects load `parties` with embedded `guests`, and guest lookup joins `parties ( party_name )` for disambiguation labels. The dashboard joins `guests` with nested `parties` and merges against a full `rsvps` select.

---

## RSVP subsystem

The RSVP flow is a **three-panel state machine** (`lookup → choose → party`) implemented in vanilla JavaScript with JSDoc-typed Supabase shapes.

### 1. Guest discovery

Input accepts first name only, last name only, or full name (`parseNameInput` splits on whitespace).

Two parallel **case-insensitive** queries run against `guests`:

- `ilike` on `first_name` with the parsed first token
- `ilike` on `last_name` with the parsed last token (or first token when last is empty)

Results merge in a `Map` keyed by `guest.id` to deduplicate rows returned by both queries.

### 2. Client-side matching

Server-side `ilike` is intentionally broad; precision happens in `guestMatchesSearch`:

- **Full name:** normalized first and last must match exactly
- **Single token:** matches either first or last name field

Normalization lowercases and trims - no phonetic or fuzzy edit distance (deliberate simplicity).

### 3. Routing


| Outcome                   | Behavior                                         |
| ------------------------- | ------------------------------------------------ |
| 0 matches                 | Error message; no party data leaked              |
| 1 unique `party_id`       | Auto-load party                                  |
| 2+ matches across parties | Render choice list with guest name + party label |


### 4. Party form & persistence

- `loadParty` fetches `parties` with nested `guests` via `.single()`
- `renderGuestFields` builds per-guest cards (attending select + dietary text)
- In-memory `responses` object syncs on `input` / `change`
- Submit maps to rows and `**upsert`s** into `rsvps` with `onConflict: "guest_id"`

Dynamic HTML uses `escapeHtml` on all interpolated guest/party strings to mitigate XSS when rendering search results and form cards.

### 5. Configuration guard

`isConfigReady()` validates URL/key placeholders before creating the Supabase client, surfacing an actionable error if credentials are missing.

### 6. Couple dashboard (`rsvpcheck/`)

`rsvp-check.js` loads all `guests` (with nested party names) and all `rsvps` in parallel, then classifies each guest into attending, not attending, or no response. Summary counts render in the page header; tables use `textContent` (not `innerHTML`) for row data. The page is omitted from site navigation and intended as a bookmark-only ops view for the couple.

---

## Front-end design system

The main site uses a **token-driven CSS architecture** (`:root` custom properties for color, typography, shadows, radius). Notable implementation choices:

- **Layered hero:** full-viewport photography, scrim, decorative Victorian decals, and overlapping "paper" story section (`z-index` stacking)
- **Section composition:** split layouts with framed photography, cream/dark/sage section bands, and lace overlap between dress-code and RSVP CTAs
- **Responsive typography:** `clamp()` scales display type; `env(safe-area-inset-*)` respects notched devices
- **Performance:** `loading="lazy"` and `decoding="async"` on non-critical images; font preconnect to Google Fonts
- **Registry page:** intentionally separate theme (`Plus Jakarta Sans`, card layout) to match vendor link-hub patterns while staying on-brand

The registry page links out to third-party checkout flows with `rel="noopener noreferrer"` - no iframe embeds or tracking scripts in the maintained source.

---

## Local development

Static ES modules require a local HTTP origin (file:// will block imports).

**Option A - VS Code (recommended)**

Parent workspace includes a launch configuration that starts Python's static server and opens Chrome:

```bash
python -m http.server 8080 --bind 127.0.0.1
```

Serve from the `fornoffwedding/` directory, then open `http://localhost:8080`.

**Option B - manual**

```bash
cd fornoffwedding
python -m http.server 8080 --bind 127.0.0.1
```

### Supabase credentials

Edit `js/supabase-config.js`:

```js
export const SUPABASE_URL = "https://<project>.supabase.co";
export const SUPABASE_ANON_KEY = "<anon-key>";
```

Comments in that file reference `NEXT_PUBLIC_SUPABASE_*` variables used elsewhere in the couple's tooling; this static site reads the same project credentials directly.

---

## RSVP deadlines

Three distinct dates, only one of which guests ever see:


| Date             | Role                                                       | Where it lives                                     |
| ---------------- | ---------------------------------------------------------- | -------------------------------------------------- |
| **Sep 30, 2026** | Published deadline shown to guests                         | `index.html` RSVP section, `faq.html` answer       |
| **Oct 1, 2026**  | Reminder text goes out to non-responding parties           | Operator runs `scripts/send_rsvp_texts.py`         |
| **Oct 14, 2026** | True cutoff; RSVP writes stop. Never shown to guests.      | `RSVP_LOCK_AT` in `js/rsvp-app.js`                 |


The published date is deliberately early so the two-week tail absorbs stragglers. `RSVP_LOCK_AT` is stored as UTC (`2026-10-15T06:59:59Z`) because Arizona stays on MST year-round; that instant is end-of-day October 14 local.

**The client-side lock is a courtesy, not enforcement.** `rsvpIsLocked()` disables the form and rejects submits, but anyone with devtools can still POST to PostgREST with the anon key. To make the cutoff real, add an RLS policy on `rsvps` that rejects writes past the date — otherwise the lock is cosmetic.

## SMS reminder blast

`scripts/send_rsvp_texts.py` is a run-once operator script (stdlib only — nothing to install). It reads `parties` with nested `guests` plus all `rsvps`, buckets each **party**, and sends one Twilio SMS per party phone number.

Bucketing: a party is `no_response` if *any* guest on the invitation has no `rsvps` row at all; otherwise `attending` if at least one guest said yes, else `not_attending`. Default target is `no_response` only.

```bash
python scripts/send_rsvp_texts.py                    # dry run — prints the plan
python scripts/send_rsvp_texts.py --send --limit 2   # live test on two parties
python scripts/send_rsvp_texts.py --send             # full send, asks to confirm
```

Successful sends are appended to `scripts/send_log.csv`, and reruns skip parties already logged, so a crash mid-blast is safe to resume. That log holds phone numbers and is gitignored. Credentials come from `.env` (see `.env.example`); the script needs the **service role** key, not the anon key.

Carrier note: the `TWILIO_FROM` number must be registered for A2P 10DLC (or be a verified toll-free number) or US carriers will filter the traffic. Registration has a multi-week lead time — start it well before the send date.

## Security & privacy considerations


| Topic                    | Approach                                                                                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Guest authentication** | None. Possession of a matching invitation name grants access to that party's RSVP form - a conscious tradeoff for frictionless guest UX on a static site. |
| **Authorization**        | Expected to be enforced via Supabase **RLS** on `guests`, `parties`, and `rsvps` (policies are not versioned in this repo).                               |
| **Admin dashboard**      | `rsvpcheck/` is unlisted but public if deployed; anyone who can read all rows via the anon key can use it. Restrict with RLS or remove before going live. |
| **XSS**                  | User-derived strings escaped before `innerHTML` assignment in RSVP rendering; dashboard rows use `textContent`.                                           |
| **Secrets**              | Only the public anon key is present; service role keys must never ship to the browser.                                                                    |
| **PII surface**          | Names and dietary notes are written to Postgres; no analytics or ad scripts in first-party pages.                                                         |


---

## Deployment

The deployable unit is **the static file tree** (HTML, CSS, JS, images). Any static host (object storage + CDN, GitHub Pages, Netlify, etc.) suffices. The RSVP feature requires:

1. HTTPS origin (Supabase API calls from the browser)
2. Valid Supabase project with populated `parties` / `guests` tables
3. RLS policies aligned with the anon client's read/write needs

No Docker, CI config, or infrastructure-as-code is checked into this repository. Remote: [github.com/mateojamez/FornoffsWebsite](https://github.com/mateojamez/FornoffsWebsite).

---

## Technology summary


| Layer        | Choice                            | Rationale                                                            |
| ------------ | --------------------------------- | -------------------------------------------------------------------- |
| Pages        | Hand-authored HTML                | Zero build step; easy content edits with stakeholders                |
| Styling      | Vanilla CSS                       | Shared tokens, no framework lock-in, ~1,370 lines of cohesive layout |
| RSVP logic   | ES modules + Supabase JS (esm.sh) | Typed client, nested PostgREST queries, upsert semantics             |
| Database/API | Supabase (PostgreSQL + PostgREST) | Managed auth/RLS, no custom backend to operate                       |
| Local server | Python `http.server`              | Built-in, cross-platform, sufficient for static + module loading     |


---

## Iteration history (selected)

Development proceeded in iterative passes with direct stakeholder feedback (commits through May 2026): initial v1 site shell → love story and visual refinement → RSVP integration with Supabase → FAQ and registry hub → reception timeline redesign → RSVP lookup and questionnaire fixes after user testing → repo cleanup (CSS consolidation, dead asset removal, Supabase dashboard dump deleted from git).

---

## License

Private project for the Fornoff/Carlson wedding. All photography and copy are personal; not licensed for reuse.