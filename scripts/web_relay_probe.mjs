// Sonde du relais média (2.2.3-e, étape e2), exécutée DANS le conteneur web : node - < web_relay_probe.mjs
// N'utilise aucune vraie session : vérifie les refus (session absente/invalide, intersite, CSRF,
// identifiant invalide), les en-têtes de non-mise en cache et la destination interne joignable.
// Sortie : une ligne JSON (aucun secret).
const BASE = "http://127.0.0.1:3000";
const ID = "11111111-2222-4333-8444-555555555555";
const FAKE = "nl_session=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const out = {};
async function probe(name, path, init = {}) {
  try {
    const r = await fetch(BASE + path, { redirect: "manual", ...init });
    const text = await r.text();
    out[name] = {
      status: r.status,
      cache: r.headers.get("cache-control"),
      cookie: r.headers.get("set-cookie") !== null,
      leak: /Bearer|nl-media-|media-delivery|X-Amz|Traceback/i.test(text),
    };
  } catch (e) {
    out[name] = { status: -1, error: String(e).slice(0, 80) };
  }
}
await probe("anonyme_apercu", `/relais/medias/${ID}/apercu`);
await probe("anonyme_diffusion", `/relais/medias/${ID}/diffusion?release=${ID}&lesson_version=${ID}`);
await probe("session_invalide", `/relais/medias/${ID}/apercu`, { headers: { cookie: FAKE } });
await probe("identifiant_invalide", `/relais/medias/pas-un-uuid/apercu`, { headers: { cookie: FAKE } });
await probe("contexte_manquant", `/relais/medias/${ID}/diffusion`, { headers: { cookie: FAKE } });
await probe("intersite", `/relais/medias/${ID}/apercu`, { headers: { cookie: FAKE, "sec-fetch-site": "cross-site" } });
await probe("origine_etrangere", `/relais/medias/${ID}/apercu`, { headers: { cookie: FAKE, origin: "https://evil.example" } });
await probe("csrf_reservation", "/relais/medias", {
  method: "POST",
  headers: { cookie: FAKE, origin: "https://evil.example", "content-type": "application/json" },
  body: "{}",
});
await probe("csrf_televersement", `/relais/medias/${ID}/contenu`, {
  method: "PUT",
  headers: { cookie: FAKE, "sec-fetch-site": "cross-site", "content-type": "application/octet-stream" },
  body: "abcd",
});
await probe("methode_interdite", `/relais/medias/${ID}/apercu`, { method: "POST", headers: { cookie: FAKE } });
try {
  const r = await fetch(`${process.env.MEDIA_DELIVERY_INTERNAL_URL}/ready`);
  out.diffusion_interne_joignable = r.status;
} catch {
  out.diffusion_interne_joignable = -1;
}
out.destination = process.env.MEDIA_DELIVERY_INTERNAL_URL ?? null;
console.log(JSON.stringify(out));
