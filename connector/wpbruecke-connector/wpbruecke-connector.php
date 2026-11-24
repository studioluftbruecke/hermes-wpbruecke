<?php
/**
 * Plugin Name:       WPBrücke Connector
 * Description:       Companion for the WPBrücke Hermes plugin: a drafter role (Contributor plus uploads), signed preview links for drafts, SEO title and description fields, update counts and a review screen where your own login publishes or applies staged changes. It never publishes on its own.
 * Version:           0.1.0
 * Requires at least: 6.5
 * Requires PHP:      7.4
 * Author:            Studio Luftbrücke
 * License:           MIT
 * License URI:       https://opensource.org/licenses/MIT
 * Text Domain:       wpbruecke-connector
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_CONNECTOR_VERSION', '0.1.0' );
define( 'WPBRUECKE_CONNECTOR_FILE', __FILE__ );

require_once __DIR__ . '/includes/role.php';
require_once __DIR__ . '/includes/preview.php';
require_once __DIR__ . '/includes/seo.php';
require_once __DIR__ . '/includes/updates.php';
require_once __DIR__ . '/includes/review.php';
require_once __DIR__ . '/includes/abilities.php';
require_once __DIR__ . '/includes/rest.php';

/**
 * Activation: the drafter role and the preview secret.
 */
function wpbruecke_activate() {
	wpbruecke_sync_role();
	wpbruecke_preview_secret_create();
	update_option( 'wpbruecke_connector_version', WPBRUECKE_CONNECTOR_VERSION, false );
}
register_activation_hook( __FILE__, 'wpbruecke_activate' );

/**
 * Deactivation: rotate the preview secret, so every preview link handed out so far stops working.
 */
function wpbruecke_deactivate() {
	wpbruecke_preview_secret_rotate();
}
register_deactivation_hook( __FILE__, 'wpbruecke_deactivate' );

/**
 * A zip uploaded over an older copy does not run the activation hook: sync the role and the secret
 * once per version.
 */
function wpbruecke_maybe_upgrade() {
	if ( get_option( 'wpbruecke_connector_version' ) !== WPBRUECKE_CONNECTOR_VERSION ) {
		wpbruecke_activate();
	}
}
add_action( 'admin_init', 'wpbruecke_maybe_upgrade' );

/**
 * The SEO plugin that owns the page head, or null.
 *
 * @return string|null yoast, rankmath, seopress or null.
 */
function wpbruecke_seo_plugin() {
	if ( defined( 'WPSEO_VERSION' ) ) {
		return 'yoast';
	}
	if ( defined( 'RANK_MATH_VERSION' ) ) {
		return 'rankmath';
	}
	if ( defined( 'SEOPRESS_VERSION' ) ) {
		return 'seopress';
	}
	return null;
}

/**
 * The review screen's URL, optionally for one post.
 *
 * @param int $post_id Post ID or 0.
 * @return string
 */
function wpbruecke_review_url( $post_id = 0 ) {
	$args = array( 'page' => 'wpbruecke-review' );
	if ( $post_id ) {
		$args['post'] = (int) $post_id;
	}
	return add_query_arg( $args, admin_url( 'admin.php' ) );
}
