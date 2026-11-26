# WooCommerce pack

**Scope:** the WooCommerce pack lists products, writes draft products (name, description, prices,
categories, featured image), and at the Publish level creates and updates live products with a
read-back; it never reads or changes orders or customers and never deletes a product. Say that sentence
when a request falls outside it; the person does such work in WooCommerce.

## When it applies

- WooCommerce is active (`wp_pack action=list site=<id>` shows `detected: true`, `wp_doctor` shows
  `pack WooCommerce: detected`), the site runs the WPBrücke connector 0.5.0 or newer, and the site file
  switches the pack on: `packs: {woocommerce: true}`. Switching it on is the person's decision (a card).
- The Hermes user holds one of the connector's shop roles. The site's admin gives it in wp-admin >
  Users (the user, **Role**, **Update User**), then the person confirms the new level on the WPBrücke
  page:
  - **WPBrücke shop drafter** is the Drafts level: new draft products, edits to its own draft products,
    and post drafts with images. It never holds `publish_products`.
  - **WPBrücke shop publisher** is the Publish level: also creates products, changes its own live
    ones, and publishes its own posts.
- WooCommerce's Shop Manager is refused (it can change theme settings and list users), and so is an
  administrator.
- The reads and the Publish-level writes run WooCommerce's own abilities, so they need WordPress 6.9 or
  newer. Tested with WooCommerce 11.3.1 on WordPress 7.1.5.

## Ops (`wp_pack action=run site=<id> pack=woocommerce op=<op> input={...}`)

No op asks for a card: reads need none, and writes run inside the site's level.

| Op | Level | Through | What it does |
|---|---|---|---|
| `products-query` | read | ability `woocommerce/products-query` (`read_private_products`) | lists products: `search`, `sku`, `status`, `stock_status`, `id`, `page`, `per_page` (default 10) |
| `draft-product` | drafts | connector route `POST shop/product` (`edit_products`) | a new draft product, or with `id` an edit of a draft simple product; always saved as a draft |
| `create-product` | publish | ability `woocommerce/product-create` (`publish_products`) | creates a product (`status: publish` makes it live), then reads it back |
| `update-product` | publish | ability `woocommerce/product-update` (`edit_published_products`) | changes a product, a live one too, then reads it back |
| `prove-product` | read | connector route `POST preview-link`, then the proof browser | the product page at the site's proof widths: a draft through the signed preview link, a live product at its permalink |

## Write a draft product, then prove it

1. Upload the featured image first (`wp_draft action=upload_media`) and keep its media id.
2. Call `op=draft-product` with, for example, `input={"name": "Linen apron", "regular_price": "24.90",
   "body": {...}, "short_description": "...", "category_ids": [16], "image_id": 18}`.
   - `body` is the description as words: `{heading, lead, points: [...], closing}`. WPBrücke lays it out
     in the site's colours and heading font, and the answer's `design` names the tokens it used. Send
     `description` (HTML) only when the person gave the HTML. Never send both.
   - Prices are decimal strings (`"19.90"`). An empty string clears a price.
   - `category_ids` are product category ids. Take them from the person or from an existing product
     (`products-query`); never guess an id.
   - `draft-product` takes no `status`. It always saves a draft.
3. The answer's `result.read_back` has `status`, `draft` and the comparison below (name, prices,
   categories, image, descriptions as WordPress stored them). Report the product id and the status, and
   every field in `dropped` or `truncated`.
4. Call `op=prove-product input={"id": <id>}`. It answers the proof's checks and the shot file names
   per width, without an image. Report each check's verdict as it came back. In the proof a draft
   product's page shows no **Add to cart** button: WooCommerce shows it on a draft only to users who
   may edit the product, and the proof browser is logged out. That is expected, not a fault.
5. At the Drafts level the person publishes the product in wp-admin > Products. Say so.

## Publish level

- `create-product` and `update-product` answer `read_back`: `status`, `fields` (name, status, prices,
  sku, stock status, permalink, the product's type), and for an update `changed`, the fields whose value
  moved. Report what read back, not what you sent.
- Every op that writes compares each field it sent with what the site kept: `as_sent`, `not_as_sent`
  (WordPress filters HTML the user may not post), and of those `dropped` (not saved at all, for example
  `stock_quantity` without `manage_stock: true`, or a sale price above the regular price) and
  `truncated` (cut short); `stored` shows what the site holds for them and `note` says it in a sentence.
  `not_compared` lists sent fields the read-back cannot see. `ok: true` means the write went through,
  not that every field was kept: when `dropped` or `truncated` is not empty, tell the person which.
- WooCommerce changes prices and stock only together with the product's type. WPBrücke reads the type
  and adds `product_type_alias` itself. Pass it only when the refusal asks for it.
- A change to a live product is visible to shoppers at once. Prove it with `prove-product` afterwards.

## Refusals and what to do

| Code | Means | Do |
|---|---|---|
| `pack_off` | the site file has the WooCommerce pack off | the person switches it on (you get a card when you ask) |
| `unknown_op` | the pack has no such op | use the ops above; say the scope sentence for anything else (orders, customers, deleting) |
| `bad_args` | the input has a key the op does not take (for example `status` on `draft-product`) | fix the input from `wp_pack action=describe pack=woocommerce op=<op>` |
| `unattended` | a scheduled job or a one-shot run | pack ops run only in a chat with the person; `list` and `describe` still work |
| `level_read` | the Hermes user's role only reads | the site's admin gives it **WPBrücke shop drafter** in wp-admin > Users; the person confirms on the WPBrücke page |
| `level_drafts` | `create-product` or `update-product` on a Drafts-level site | say the draft is the limit here; the person publishes in wp-admin > Products, or the admin gives the role **WPBrücke shop publisher** |
| `level_unconfirmed`, `level_stopped` | the level is not confirmed, or the user's rights changed | the person confirms on the WPBrücke page; nothing runs until then |
| `route_not_found` | the site has no connector, or its connector is older than 0.5.0 (the message names its version), or `wp_code: wpbruecke_not_found` (no such product) | the person updates the connector (**Download connector** on the WPBrücke page); for a missing product, check the id with `products-query` |
| `route_forbidden` with `wp_code: wpbruecke_cannot_edit_product` | another user's product, or a published one | nothing changed; the person changes it in WooCommerce |
| `route_forbidden` with `wp_code: wpbruecke_not_draft` | `draft-product` with the id of a product that is not a draft | at the Publish level use `update-product`; at the Drafts level the person changes it |
| `route_forbidden` with `wp_code: wpbruecke_cannot_assign_terms` | the user may not assign product categories | send the draft without `category_ids`; the person sets them |
| `route_invalid_input` | the input was refused: `wpbruecke_bad_input` (an empty name, a price that is not a decimal, an id that is no product category), `wpbruecke_bad_image` (an image the user cannot read), or both `body` and `description` | fix the input as the message says |
| `route_failed` with `wp_code: wpbruecke_product_type` | the draft is not a simple product (variable, grouped, external) | the person edits it in WooCommerce |
| `route_failed` with `wp_code: wpbruecke_no_woocommerce` | WooCommerce is not active | say so; nothing changed |
| `ability_forbidden` | WooCommerce refused the ability for this user; the message names the permission | name the user and the permission; the site's admin decides in wp-admin |
| `ability_invalid_input` | WooCommerce did not accept the input, or `update-product` has no `id` | fix the input from `describe`; pass `product_type_alias` when the message asks for it |
| `ability_not_found` | the site has no Abilities API (WordPress older than 6.9), or the WooCommerce release has no such ability | the person updates WordPress or WooCommerce; `draft-product` still works through the connector route |
| "Publishing products needs the publish_products capability" (`wpbruecke_publish_forbidden`; over WooCommerce's API `woocommerce_rest_product_not_created`) | the connector's publish guard stopped a save that would publish | the user cannot publish products; stop and tell the person; never retry with another status |

Any other code carries the site's own sentence; repeat it. Every write and refusal lands in the write
log (`wp_doctor action=log`, tool `wp_pack`, the product name as its label).

## What the pack never does

- It never reads or changes orders, refunds, customers, coupons or store settings, and never deletes a
  product. WooCommerce's abilities for those are seen but not enabled (`wp_doctor` lists them).
- It never publishes at the Drafts level and never edits another user's product.
- It never changes variations or a product's type. The person does that in WooCommerce.
