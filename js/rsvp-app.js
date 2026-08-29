import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";
import { SUPABASE_URL, SUPABASE_ANON_KEY } from "./supabase-config.js";

function isConfigReady() {
  const urlOk =
    SUPABASE_URL &&
    !String(SUPABASE_URL).includes("your_supabase_project_url") &&
    SUPABASE_URL.startsWith("http");
  const keyOk =
    SUPABASE_ANON_KEY &&
    !String(SUPABASE_ANON_KEY).includes("your_supabase_anon_key") &&
    String(SUPABASE_ANON_KEY).length > 20;
  return urlOk && keyOk;
}

/**
 * Internal hard cutoff for RSVP writes. Deliberately later than the date
 * shown on the site (September 30): the public date drives responses, this
 * one is the real close for the caterer, leaving a grace window for
 * stragglers chased by the October 1 text. Not displayed to guests.
 *
 * Stored as UTC because Arizona does not observe DST (MST = UTC-7 all year),
 * so this is end-of-day October 14, 2026 local time.
 */
const RSVP_LOCK_AT = new Date("2026-10-15T06:59:59Z");

function rsvpIsLocked() {
  return Date.now() >= RSVP_LOCK_AT.getTime();
}

const LOCKED_MESSAGE =
  "R.S.V.P.s are now closed and the final headcount has gone to our vendors. " +
  "Please reach out to Taylor or Connor directly if you need to reach them.";

const els = {
  lookupPanel: document.getElementById("rsvp-lookup"),
  choosePanel: document.getElementById("rsvp-choose"),
  partyPanel: document.getElementById("rsvp-party"),
  formFind: document.getElementById("form-find"),
  formRsvp: document.getElementById("form-rsvp"),
  guestName: document.getElementById("guest-name"),
  guestChoices: document.getElementById("guest-choices"),
  partyTitle: document.getElementById("party-title"),
  guestFields: document.getElementById("guest-fields"),
  message: document.getElementById("rsvp-message"),
  btnReset: document.getElementById("btn-reset-party"),
  btnBackLookup: document.getElementById("btn-back-lookup"),
};

let supabase = null;
/** @type {{ id: string, party_name: string, guests: { id: string, first_name: string, last_name: string | null }[] } | null} */
let party = null;
const responses = {};

function setMessage(text, isError = false) {
  els.message.textContent = text;
  els.message.classList.toggle("rsvp-message--error", isError);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function normalizeNamePart(value) {
  return String(value ?? "").trim().toLowerCase();
}

/** @param {string} input */
function parseNameInput(input) {
  const trimmed = input.trim();
  if (!trimmed) return { first: "", last: "" };
  const parts = trimmed.split(/\s+/);
  return {
    first: parts[0],
    last: parts.slice(1).join(" "),
  };
}

/** @param {{ first_name: string, last_name?: string | null }} guest */
function formatGuestName(guest) {
  const first = String(guest.first_name ?? "").trim();
  const last = guest.last_name == null ? "" : String(guest.last_name).trim();
  return last ? `${first} ${last}` : first;
}

/**
 * @param {{ first_name: string, last_name?: string | null }} guest
 * @param {string} userFirst
 * @param {string} userLast
 */
function guestMatchesSearch(guest, userFirst, userLast) {
  const guestFirst = normalizeNamePart(guest.first_name);
  const guestLast =
    guest.last_name == null || String(guest.last_name).trim() === ""
      ? null
      : normalizeNamePart(guest.last_name);
  const uFirst = normalizeNamePart(userFirst);
  const uLast = userLast ? normalizeNamePart(userLast) : "";

  if (!uFirst) return false;

  if (uLast) {
    if (guestFirst !== uFirst) return false;
    if (guestLast === null) return false;
    return guestLast === uLast;
  }

  const token = uFirst;
  if (guestFirst === token) return true;
  if (guestLast !== null && guestLast === token) return true;
  return false;
}

const GUEST_LOOKUP_SELECT = `
  id,
  first_name,
  last_name,
  party_id,
  parties (
    party_name
  )
`;

/** @param {import("@supabase/supabase-js").SupabaseClient} db */
async function fetchGuestCandidates(db, userFirst, userLast) {
  const byFirst = await db
    .from("guests")
    .select(GUEST_LOOKUP_SELECT)
    .ilike("first_name", userFirst);

  if (byFirst.error) return { data: null, error: byFirst.error };

  const byLast = await db
    .from("guests")
    .select(GUEST_LOOKUP_SELECT)
    .ilike("last_name", userLast || userFirst);

  if (byLast.error) return { data: null, error: byLast.error };

  const merged = new Map();
  for (const guest of [...(byFirst.data ?? []), ...(byLast.data ?? [])]) {
    merged.set(guest.id, guest);
  }
  return { data: [...merged.values()], error: null };
}

function renderGuestFields(guests) {
  Object.keys(responses).forEach((k) => delete responses[k]);

  els.guestFields.innerHTML = guests
    .map(
      (g) => `
    <article class="rsvp-guest-card" data-guest-id="${escapeHtml(g.id)}">
      <h2 class="rsvp-guest-card__name">${escapeHtml(formatGuestName(g))}</h2>
      <label class="rsvp-field">
        <span class="rsvp-field__label">Attending?</span>
        <select class="rsvp-input" required data-guest-id="${escapeHtml(g.id)}" data-field="attending">
          <option value="">Select one</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </label>
      <label class="rsvp-field">
        <span class="rsvp-field__label">Dietary restrictions</span>
        <input class="rsvp-input" type="text" placeholder="Allergies or dietary needs" data-guest-id="${escapeHtml(g.id)}" data-field="dietary_restrictions" />
      </label>
    </article>
  `
    )
    .join("");

  els.guestFields.querySelectorAll("[data-field]").forEach((input) => {
    const guestId = input.getAttribute("data-guest-id");
    const field = input.getAttribute("data-field");
    const sync = () => {
      responses[guestId] = responses[guestId] || {};
      responses[guestId][field] = input.value;
    };
    input.addEventListener("input", sync);
    input.addEventListener("change", sync);
  });
}

function showLookup() {
  party = null;
  els.lookupPanel.hidden = false;
  els.choosePanel.hidden = true;
  els.partyPanel.hidden = true;
  els.formFind.reset();
  setMessage("");
}

function showParty(p) {
  party = p;
  els.lookupPanel.hidden = true;
  els.choosePanel.hidden = true;
  els.partyPanel.hidden = false;
  els.partyTitle.textContent = `RSVP for ${p.party_name}`;
  renderGuestFields(p.guests || []);
  applyLockState();
}

/**
 * After the internal cutoff, a guest can still look up their invitation and
 * see what they submitted, but every control is inert so nobody fills out a
 * form that will only be rejected on submit.
 */
function applyLockState() {
  if (!rsvpIsLocked()) return;

  const submitBtn = els.formRsvp.querySelector('button[type="submit"]');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "R.S.V.P.s are closed";
  }
  els.guestFields
    .querySelectorAll("select, input, textarea")
    .forEach((field) => {
      field.disabled = true;
    });
  setMessage(LOCKED_MESSAGE, true);
}

/** @param {Array<{ id: string, first_name: string, last_name?: string | null, party_id: string, parties?: { party_name: string } | { party_name: string }[] | null }>} matches */
function showGuestChoices(matches) {
  els.lookupPanel.hidden = true;
  els.choosePanel.hidden = false;
  els.partyPanel.hidden = true;
  setMessage("");

  els.guestChoices.innerHTML = matches
    .map((guest) => {
      const partyName = Array.isArray(guest.parties)
        ? guest.parties[0]?.party_name
        : guest.parties?.party_name;
      const partyLabel = partyName ? escapeHtml(partyName) : "Invitation";
      return `
        <li>
          <button type="button" class="rsvp-choice" data-party-id="${escapeHtml(guest.party_id)}">
            <span class="rsvp-choice__name">${escapeHtml(formatGuestName(guest))}
            </span>
            <span class="rsvp-choice__party">${partyLabel}</span>
          </button>
        </li>
      `;
    })
    .join("");

  els.guestChoices.querySelectorAll(".rsvp-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      const partyId = btn.getAttribute("data-party-id");
      if (partyId) loadParty(partyId);
    });
  });
}

async function loadParty(partyId) {
  if (!supabase) return;

  const { data: partyData, error } = await supabase
    .from("parties")
    .select(
      `
        id,
        party_name,
        guests (
          id,
          first_name,
          last_name
        )
      `
    )
    .eq("id", partyId)
    .single();

  if (error || !partyData) {
    console.error("Party lookup failed:", error);
    setMessage("Could not load your invitation. Please try again.", true);
    return;
  }

  setMessage("");
  showParty(partyData);
}

async function findParty(e) {
  e.preventDefault();
  if (!supabase) {
    setMessage(
      "RSVP is not configured yet. Add your Supabase URL and anon key in js/supabase-config.js (same values as NEXT_PUBLIC_* in .env.local).",
      true
    );
    return;
  }

  const { first: userFirst, last: userLast } = parseNameInput(els.guestName.value);
  if (!userFirst) {
    setMessage("Please enter your name.", true);
    return;
  }

  const { data: candidates, error } = await fetchGuestCandidates(
    supabase,
    userFirst,
    userLast
  );

  if (error) {
    console.error("Guest lookup failed:", error);
    setMessage("No invitation found. Please check the name on your invitation.", true);
    return;
  }

  const matches = (candidates ?? []).filter((guest) =>
    guestMatchesSearch(guest, userFirst, userLast)
  );

  if (matches.length === 0) {
    setMessage("No invitation found. Please check the name on your invitation.", true);
    return;
  }

  const uniqueMatches = [...new Map(matches.map((g) => [g.id, g])).values()];
  const partyIds = new Set(uniqueMatches.map((g) => g.party_id));

  if (partyIds.size === 1) {
    setMessage("");
    await loadParty(uniqueMatches[0].party_id);
    return;
  }

  showGuestChoices(uniqueMatches);
}

async function submitRSVP(e) {
  e.preventDefault();
  if (!supabase || !party) return;

  if (rsvpIsLocked()) {
    setMessage(LOCKED_MESSAGE, true);
    return;
  }

  const guests = party.guests || [];
  const rows = guests.map((guest) => ({
    guest_id: guest.id,
    attending: responses[guest.id]?.attending === "yes",
    dietary_restrictions: responses[guest.id]?.dietary_restrictions || "",
    updated_at: new Date().toISOString(),
  }));

  const { error } = await supabase.from("rsvps").upsert(rows, { onConflict: "guest_id" });

  if (error) {
    console.error("RSVP upsert failed:", error);
    const detail = error.message || String(error);
    setMessage(`Could not save RSVP: ${detail}`, true);
    return;
  }

  setMessage("Thank you! Your RSVP has been saved.");
}

function init() {
  if (isConfigReady()) {
    supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  }

  els.formFind.addEventListener("submit", findParty);
  els.formRsvp.addEventListener("submit", submitRSVP);
  els.btnReset.addEventListener("click", (e) => {
    e.preventDefault();
    showLookup();
  });
  els.btnBackLookup.addEventListener("click", (e) => {
    e.preventDefault();
    showLookup();
  });
}

init();
