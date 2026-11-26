# GeneratePress pack

**Scope:** the GeneratePress pack builds new pages as drafts (GenerateBlocks, else core blocks) in a
build run and stages brand colours for the person's click; it does not edit existing pages or theme
settings. Say that sentence when a request falls outside it.

## When it applies

- The site's theme is GeneratePress or a child of it (`wp_doctor` shows `pack GeneratePress: detected`).
- The site file switches it on: `packs: {generatepress: true}`. Switching a pack on widens what Hermes
  may write, so `wp_sites update` of `packs` shows the person a card and is refused in scheduled jobs
  and one-shot runs. Do not ask for it on your own; offer it when the person wants a build run.
- `wp_build` refuses every action while the pack is off and names the switch.

## A build run

1. `wp_build action=start site=<id> brief={name, audience, must_have[], tone}`. The mode follows the
   site: `generateblocks` when GenerateBlocks is active, else `core`.
2. The brief, brand, sitemap and every later gate are the person's clicks on the WPBrücke page.
   `wp_build action=get` shows which step waits on whom; never claim a gate passed.
3. `set_brand` (colors [{slug, name, color}], fonts {body, headings}) stages the brand through the
   connector after the person's card; it reaches GeneratePress only when they click Apply brand on the
   connector's review screen. Without the connector they set the colours in the Customizer.
4. `build_page` (run, slug, title, sections) writes each page as a draft on the desk; then `wp_proof`
   the desk item. Sections: hero, features, text, cta, contact.
5. `handoff` gives the client's guide. Going live is the person's click in WordPress.

## Design tokens

`wp_sites get` returns `design`: the theme's colours (GeneratePress' global colours through the
connector's read-only `gp-brand-get`), fonts and logo, read live. The site file's `design:` block holds
only what the person changed; keys it overrides are listed in `overridden`. Use these tokens for new
pages so they match the site; never copy the theme's tokens into the site file.

## Blocks

While the pack is on, GenerateBlocks (`generateblocks/*`) and GeneratePress blocks count as known: an
edit may change them like core blocks. Other plugins' blocks still need `drop_blocks` to be dropped.
