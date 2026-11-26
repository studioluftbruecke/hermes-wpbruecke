# Elementor pack

**Scope:** the Elementor pack changes texts, links and images of existing heading, text-editor, button,
image, icon-list and call-to-action widgets, through a staged copy; it never adds, removes or moves an
element, never creates an Elementor page, and leaves every other widget and every V4 atomic element as
it is. Say that sentence when a request falls outside it; the person makes such changes in Elementor.

## When it applies

- Elementor is active (`wp_doctor` shows `pack Elementor: detected`), the site runs the WPBrücke
  connector 0.4.0 or newer (its Elementor routes), and the site file switches it on:
  `packs: {elementor: true}`. Switching it on is the person's decision (a card).
- With the pack off, an Elementor page keeps its body (`builder_page`); title, excerpt, SEO fields and the
  featured image still work.
- The pack is tested on the Elementor releases its refusal names (`elementor_untested`). Outside
  that range every Elementor page is read-only for its body; say so and name
  the range. Title, excerpt and SEO fields still work.

## Change a page

1. `wp_content get` the page. On an Elementor page it answers `content_format: "elementor"` and
   `elementor.elements`: per element its `id`, `widgetType`, `editable` (the paths you may set) with
   their `values`, or `read_only` with the reason. `content` is empty: the stored body is only
   Elementor's plain-text fallback, never edit it.
2. Published page: `wp_draft action=stage id=<page> elementor_edits=[...]`. A draft (or a copy you
   staged before): `wp_draft action=update id=<draft> elementor_edits=[...]`. Each edit is
   `{"element_id": "e4f5a6b", "set": {"title": "New heading"}}`; one entry per element, any number of
   its editable paths. Paths:
   - text: `title`, `editor` (HTML), `text`, `caption`, `description`, `button`, `ribbon_title`
   - link: `link.url`, `link.is_external`, `link.nofollow` (true or false)
   - image: `image.id` (a media library id; the URL follows), or `image.url` plus `image.alt` for an
     image from outside the library (a library image shows its media item's alt text); the
     call-to-action widget has `bg_image.*` and `graphic_image.*` the same way
   - icon list items by their `_id`: `icon_list.<_id>.text`, `icon_list.<_id>.link.url`,
     `icon_list.<_id>.link.is_external`, `icon_list.<_id>.link.nofollow`
   Title, excerpt and SEO go in the same call as usual (`title`, `excerpt`, `seo`).
3. The answer carries `elementor.edited`, `elementor.hash` and `elementor.read_back`: WordPress saved the
   copy and the pack read it back. Then `wp_proof item=<desk item>`: the signed preview shows the copy with its own CSS and page
   template (a Canvas page shows no theme header).
4. At the publish level, `wp_draft action=apply item=<desk item>` copies the layout, title, excerpt, SEO
   fields, template and featured image onto the live page, clears Elementor's caches for it and moves the
   copy to the trash; report `confirmed`, `content_matches` and `copy_deleted`. Below that level the person clicks
   **Apply copy to the live post** on the connector's review screen (the desk links it); it runs the same apply.

## Refusals and what to do

| Code | Means | Do |
|---|---|---|
| `elementor_read_only` | the element or path is outside the scope (another widget, a layout element, a V4 atomic element) | say the scope sentence; the person changes it in Elementor |
| `elementor_structure` | the edit names an element or list item that does not exist, or would add or remove one | use the ids `wp_content get` lists; never add items |
| `elementor_dynamic` | the setting comes from a dynamic tag (`dynamic` in the tree) | the person changes it in Elementor |
| `elementor_unknown_dynamic` | the page has a dynamic tag the site's Elementor cannot resolve (its plugin, e.g. Elementor Pro, is off); Elementor's save would delete it | the page is read-only here; the person edits it in Elementor with that plugin active |
| `elementor_alt` | `*.alt` sent for a media library image | the alt text lives on the media item; or give an outside image by `*.url` with its alt |
| `elementor_untested` | Elementor's release is outside the tested range | the body is read-only; title and SEO still work |
| `elementor_scope` | `content` was sent for an Elementor page | send `elementor_edits` instead |
| `elementor_readback` | the saved copy does not read back as sent | nothing live changed; tell the person to look at the draft in Elementor |
| `elementor_error` | Elementor itself failed while saving or copying the page (a PHP error inside it); nothing was written | tell the person to open the page in Elementor; never retry the same edit |
| `kses_would_strip` | WordPress would strip HTML elsewhere on the page for this user (no `unfiltered_html`, e.g. a multisite Editor) | a user with that right makes the edit; never try to work around it |
| `live_changed` | someone edited the live page after the copy was staged | stage the change again from the live page |
| `drafter_page` | the drafter role never edits pages | an Editor's key stages page changes; the drafter edits its own posts |
| `needs_connector` | the connector has no Elementor routes | the person updates the connector to 0.4.0 or newer (the WPBrücke page offers **Download connector**) |
| `forbidden` | WordPress refused the request for this site's user | name the user and the right; never retry with other arguments |

Any other `elementor_*` code carries the connector's own sentence; repeat it. Every write and refusal
lands in the write log, so "what happened with <page>" is answered with `wp_doctor action=log`.
