# FluentCRM pack

**Scope:** the FluentCRM pack drafts email campaigns in the site's design, previews them and sends test
copies to the address the site's admin saved; it schedules a campaign only where the admin switched
sending on, after a card every time, and it never sends to a contact, never changes another campaign
status, and never writes contacts, lists, tags or automations. Say that sentence when a request falls
outside it; the person does the rest in FluentCRM.

## When it applies

- FluentCRM (free or Pro) is active (`wp_doctor` shows `pack FluentCRM: detected`), the site runs the
  WPBrücke connector 0.5.0 or newer, and the site file switches the pack on: `packs: {fluentcrm: true}`.
  Switching it on is the person's decision (a card). Tested with FluentCRM 3.3.5 and FluentCRM Pro
  3.3.1 on WordPress 7.1.5.
- What Hermes may do is the site admin's setting in wp-admin > Settings > WPBrücke FluentCRM, for the
  Hermes user: **View campaigns**, **Write draft campaigns**, **Send test emails** (to the address saved
  there) and **Schedule and send campaigns**. `wp_doctor` shows the boxes in its line `FluentCRM
  permissions (...)` and as `fluentcrm` per site. Until the admin saved that screen for the user, every
  op is refused with the sentence naming the screen. Say it and stop.
- While **Schedule and send campaigns** is off, the Hermes user holds only FluentCRM's "Read emails"
  permission, so FluentCRM itself refuses it every send. Drafts, previews and test emails go through
  three connector routes that lend "Write and send emails" for one fixed FluentCRM call each;
  previews and test emails also borrow "Read contacts" to render against the Hermes user's own contact.
- An empty business address in FluentCRM > Settings > Business Settings leaves a stray "," in every
  email's footer. When a preview shows it, tell the person where to fill the address in.

## Ops (`wp_pack action=run site=<id> pack=fluentcrm op=<op> input={...}`)

| Op | Level | Gate | Needs (box) | Through | What it does |
|---|---|---|---|---|---|
| `crm-context` | read | none | View campaigns | ability `fluent-crm/get-crm-context` | who Hermes is in FluentCRM, the sender, tag and list names (`include: ["segments", "sender", "email_blocks"]`) |
| `list-campaigns` | read | none | View campaigns | ability `fluent-crm/list-campaigns` | campaigns with status and send times (`per_page`, default 20) |
| `get-campaign` | read | none | View campaigns | ability `fluent-crm/get-campaign` | one campaign: content, sender, audience, schedule (`body_format=html` to edit it) |
| `draft-campaign` | drafts | none for a new draft or the Hermes user's own; a card for another person's draft | Write draft campaigns | connector route `POST fluentcrm/campaign` | creates a draft campaign or updates a draft |
| `preview-campaign` | drafts | none | Write draft campaigns | connector route `POST fluentcrm/preview`, then the proof browser | FluentCRM's rendered email at 600 and 390 px with link, image and contrast checks, on the campaign's desk item |
| `send-test` | drafts | none | Send test emails | connector route `POST fluentcrm/test` | one test copy to the address the admin saved, no other address |
| `schedule-campaign` | drafts | a card every time, never unattended | Schedule and send campaigns | ability `fluent-crm/change-campaign-status` (action `schedule`, fixed) | schedules a draft campaign, or sends it right after the approval |

## Draft, prove, test

1. Call `op=crm-context input={"include": ["segments", "sender"]}` for the sender and the tag and list
   names. Never ask for contacts; the pack reads none.
2. Call `op=draft-campaign` with `title`, `email_subject`, `email_pre_header`, `sections` and
   `recipients` `{tags, lists}` (by id, slug or title from step 1).
   - Each section is `heading`, `text` (plain text, a blank line between paragraphs, SmartCodes work),
     an optional `image` `{url, alt}` and an optional `button` `{text, url}`. `headline` and `intro`
     set the email's first heading and the paragraphs under it.
   - WPBrücke builds the body as block markup in the site's colours and fonts (`design_template`
     `simple`, `plain` or `classic`; default `simple`). It never writes raw HTML.
   - The answer has `campaign_id`, `desk_item`, `editor_url` and `design`.
   - To change a draft, pass its `campaign_id` with new `sections`, or with `email_body` from
     `get-campaign body_format=html` edited in place. Never send both `sections` and `email_body`.
   - Another person's draft asks the person with a card first: saving replaces their draft.
3. Call `op=preview-campaign input={"campaign_id": N}`. Report the proof's verdict and each check.
   SmartCodes in the preview and in test copies are filled from the Hermes user's own FluentCRM
   contact, never from a customer's (the connector recreates that contact when it was deleted).
4. Call `op=send-test input={"campaign_id": N}`. It sends one copy to the address the admin saved.
   Tell the person to look in that inbox (and its spam folder). Another address is never possible.
5. Tell the person the draft waits in FluentCRM: the desk item links to the campaign editor
   (`editor_url`), where they send it while **Schedule and send campaigns** is off.

## Schedule

Call `op=schedule-campaign input={"campaign_id": N, "scheduled_at": "2026-12-01 09:00:00"}` only when
the person asks for a send. It runs only where the admin ticked **Schedule and send campaigns**, only in
a chat with the person, and shows a card with the campaign, its subject, the send time, FluentCRM's
recipient estimate and the audience, every time. `scheduled_at` is ISO 8601 with an offset, or
`YYYY-MM-DD HH:MM:SS` in the site's time, at least a minute ahead. Without it FluentCRM sends right after
the approval. An approved card covers that one call only. Before you call it, say who receives the
email and when. Pausing, unscheduling or deleting a campaign is the person's, in FluentCRM.

## Refusals and what to do

| Code | Means | Do |
|---|---|---|
| `needs_permission` | the box the op needs is off, or the screen was never saved for the Hermes user | repeat the sentence: the site's admin ticks the box in wp-admin > Settings > WPBrücke FluentCRM and clicks **Save** |
| `needs_unknown` | WPBrücke could not read the user's FluentCRM permissions; nothing ran | read `wp_doctor`'s `FluentCRM permissions` line; an older connector needs 0.5.0 (**Download connector** on the WPBrücke page) |
| `not_approved` | a card op ran without an answered card for this exact call (or the person declined) | nothing was sent; ask the person again only if they still want it |
| `unattended` | a scheduled job or a one-shot run | pack ops run only in a chat with the person; `list` and `describe` still work |
| `level_read` | the Hermes user's role only reads | the site's admin gives it a drafts-level role (Contributor, WPBrücke drafter, a shop role); the person confirms on the WPBrücke page |
| `pack_off` | the site file has the FluentCRM pack off | the person switches it on (you get a card when you ask) |
| `unknown_op` | the pack has no such op | use the ops above; say the scope sentence for anything else (contacts, lists, tags, automations, pausing) |
| `bad_args` | the input has a key the op does not take (for example `to_email` on `send-test`) | fix the input from `wp_pack action=describe pack=fluentcrm op=<op>` |
| `route_invalid_input` | the draft's input is incomplete or wrong: a new draft without `title` or without `sections`, both `sections` and `email_body`, markup FluentCRM's editor would not keep, a `design_template` other than `simple`, `plain` or `classic` | fix the input as the message says |
| `route_forbidden` with `wp_code: wpbruecke_fcrm_box_off` | the connector found the box off for this user (the admin changed it within the last minute) | the site's admin ticks the box; nothing ran |
| `route_failed` with `wp_code: wpbruecke_fcrm_not_draft` | the campaign is not a draft (scheduled, sending or sent) | never change it; the person handles it in FluentCRM |
| `route_not_found` with `wp_code: wpbruecke_fcrm_no_campaign` | no campaign with that id | check the id with `list-campaigns` |
| `route_failed` with `wp_code: wpbruecke_fcrm_no_ability` | FluentCRM's abilities are missing (WordPress 6.9 or newer, FluentCRM 3.3 or newer, and FluentCRM's abilities switched on) | the person updates WordPress or FluentCRM, or turns FluentCRM's MCP server switch back on (on by default) |
| `route_failed` with `wp_code: wpbruecke_fcrm_inactive` | FluentCRM is not active | say so; nothing changed |
| `route_failed` with `wp_code: wpbruecke_fcrm_no_own_contact` | the connector could not find or create the Hermes user's own FluentCRM contact, so nothing was rendered or sent (SmartCodes would fill from a real customer) | say so; the site's admin checks the Hermes user's email address and saves Settings > WPBrücke FluentCRM again |
| `ability_forbidden` | FluentCRM refused the ability for this user; the message names the permission | the box that grants it is the admin's; never look for another way |
| `ability_invalid_input`, `ability_failed` | FluentCRM refused the call with its own sentence (for example "No recipients found for this campaign") | repeat it; for no recipients, set `recipients` on the draft first |
| `ability_not_found` | the site has no Abilities API (WordPress older than 6.9), or FluentCRM's MCP server switch (on by default) is off | the person updates WordPress or turns that switch back on in FluentCRM's settings |

Any other code carries the site's own sentence; repeat it. Every write and refusal lands in the write
log (`wp_doctor action=log`, tool `wp_pack`, the campaign title or subject as its label).

## What the pack never does

- It never sends to a single contact (`send-email-to-contact` needs a contact permission the connector
  never grants) and never sends a test copy to any address but the saved one.
- It never pauses, resumes, unschedules, cancels or deletes a campaign. Of FluentCRM's status changes
  it runs only `schedule`, behind a card.
- It never reads or writes contacts, lists, tags, sequences or automations. Tag and list names come
  from `crm-context`.
- It never schedules unattended, and an approved card never covers the next call.
