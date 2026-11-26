<?php
/**
 * Signed, time-limited preview links for drafts.
 *
 * A link carries the post id, an expiry and an HMAC over "id|post_modified_gmt|expiry" made with a
 * per-site secret (option wpbruecke_preview_secret, not autoloaded). It stops working when it
 * expires (2 hours), when the post is saved again (the modified time changes), when the post is
 * published, and when the connector is deactivated (the secret is rotated). Hermes keeps the post id,
 * never the link.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_PREVIEW_TTL', 2 * HOUR_IN_SECONDS );

/**
 * Post statuses a preview link may show.
 *
 * @return string[]
 */
function wpbruecke_preview_statuses() {
	return array( 'draft', 'pending', 'future' );
}

/**
 * A new random secret: 32 bytes as 64 hex characters.
 *
 * @return string
 */
function wpbruecke_new_secret() {
	return bin2hex( random_bytes( 32 ) );
}

/**
 * Create the secret if it does not exist (autoload off).
 */
function wpbruecke_preview_secret_create() {
	if ( ! get_option( 'wpbruecke_preview_secret' ) ) {
		delete_option( 'wpbruecke_preview_secret' );
		add_option( 'wpbruecke_preview_secret', wpbruecke_new_secret(), '', false );
	}
}

/**
 * Replace the secret: every link handed out so far dies.
 */
function wpbruecke_preview_secret_rotate() {
	update_option( 'wpbruecke_preview_secret', wpbruecke_new_secret(), false );
}

/**
 * The secret, created on first use if activation did not run.
 *
 * @return string
 */
function wpbruecke_preview_secret() {
	$secret = get_option( 'wpbruecke_preview_secret' );
	if ( ! is_string( $secret ) || strlen( $secret ) < 64 ) {
		wpbruecke_preview_secret_create();
		$secret = (string) get_option( 'wpbruecke_preview_secret' );
	}
	return $secret;
}

/**
 * The token for one post state and expiry.
 *
 * @param int    $post_id Post ID.
 * @param string $marker  The post's post_modified_gmt.
 * @param int    $expires Unix time.
 * @return string
 */
function wpbruecke_preview_token( $post_id, $marker, $expires ) {
	return hash_hmac( 'sha256', (int) $post_id . '|' . $marker . '|' . (int) $expires, wpbruecke_preview_secret() );
}

/**
 * A signed preview link for a draft, pending or scheduled post.
 *
 * @param WP_Post|int $post Post.
 * @return array|WP_Error {url, expires_at}
 */
function wpbruecke_preview_link( $post ) {
	$post = get_post( $post );
	if ( ! $post ) {
		return new WP_Error( 'wpbruecke_not_found', __( 'No such post.', 'wpbruecke-connector' ), array( 'status' => 404 ) );
	}
	if ( ! in_array( $post->post_status, wpbruecke_preview_statuses(), true ) ) {
		return new WP_Error(
			'wpbruecke_not_previewable',
			__( 'Preview links are only for drafts, pending and scheduled posts.', 'wpbruecke-connector' ),
			array( 'status' => 409 )
		);
	}
	$expires = time() + WPBRUECKE_PREVIEW_TTL;
	$token   = wpbruecke_preview_token( $post->ID, $post->post_modified_gmt, $expires );
	$args    = array();
	if ( 'page' === $post->post_type ) {
		$args['page_id'] = $post->ID;
	} else {
		$args['p'] = $post->ID;
		if ( 'post' !== $post->post_type ) {
			$args['post_type'] = $post->post_type;
		}
	}
	$args['wpb_preview'] = $token;
	$args['wpb_exp']     = $expires;
	return array(
		'url'        => add_query_arg( $args, home_url( '/' ) ),
		'expires_at' => gmdate( 'c', $expires ),
	);
}

/**
 * The post id the current request asks for, from p or page_id.
 *
 * @return int
 */
function wpbruecke_preview_request_id() {
	// phpcs:disable WordPress.Security.NonceVerification.Recommended -- the HMAC token is the check.
	if ( isset( $_GET['page_id'] ) ) {
		return absint( wp_unslash( $_GET['page_id'] ) );
	}
	if ( isset( $_GET['p'] ) ) {
		return absint( wp_unslash( $_GET['p'] ) );
	}
	// phpcs:enable
	return 0;
}

/**
 * Check the request's token. Returns the post on success, null otherwise.
 *
 * @return WP_Post|null
 */
function wpbruecke_preview_validate() {
	// phpcs:disable WordPress.Security.NonceVerification.Recommended -- the HMAC token is the check.
	$token   = isset( $_GET['wpb_preview'] ) ? sanitize_text_field( wp_unslash( $_GET['wpb_preview'] ) ) : '';
	$expires = isset( $_GET['wpb_exp'] ) ? absint( wp_unslash( $_GET['wpb_exp'] ) ) : 0;
	// phpcs:enable
	$post_id = wpbruecke_preview_request_id();
	$now     = time();
	if ( ! preg_match( '/^[a-f0-9]{64}$/', $token ) || ! $post_id || $expires < $now || $expires > $now + WPBRUECKE_PREVIEW_TTL + 60 ) {
		return null;
	}
	$post = get_post( $post_id );
	if ( ! $post || ! in_array( $post->post_status, wpbruecke_preview_statuses(), true ) ) {
		return null;
	}
	$want = wpbruecke_preview_token( $post->ID, $post->post_modified_gmt, $expires );
	if ( ! hash_equals( $want, $token ) ) {
		return null;
	}
	return $post;
}

/**
 * The post a valid link opened in this request (or null).
 *
 * @param WP_Post|null|false $set Set the value when not false.
 * @return WP_Post|null
 */
function wpbruecke_preview_current( $set = false ) {
	static $current = null;
	if ( false !== $set ) {
		$current = $set;
	}
	return $current;
}

/**
 * Main front-end query with wpb_preview: validate, then let that one draft through.
 *
 * @param WP_Query $query Query.
 */
function wpbruecke_preview_pre_get_posts( $query ) {
	if ( is_admin() || ! $query->is_main_query() || ! isset( $_GET['wpb_preview'] ) ) { // phpcs:ignore WordPress.Security.NonceVerification.Recommended
		return;
	}
	$post = wpbruecke_preview_validate();
	if ( ! $post ) {
		wp_die(
			esc_html__( 'This preview link is not valid any more. Ask for a new one.', 'wpbruecke-connector' ),
			esc_html__( 'Preview link expired', 'wpbruecke-connector' ),
			array( 'response' => 403 )
		);
	}
	wpbruecke_preview_current( $post );
	$query->set( 'post_type', $post->post_type );
	$query->set( 'post_status', wpbruecke_preview_statuses() );
	if ( ! defined( 'DONOTCACHEPAGE' ) ) {
		// Page-cache plugins (WP Super Cache, W3 Total Cache, WP Rocket, LiteSpeed Cache) read this exact shared name; a prefixed one would cache the preview.
		define( 'DONOTCACHEPAGE', true ); // phpcs:ignore WordPress.NamingConventions.PrefixAllGlobals.NonPrefixedConstantFound
	}
	add_filter( 'redirect_canonical', '__return_false' );
	add_filter( 'wp_robots', 'wp_robots_no_robots' );
}
add_action( 'pre_get_posts', 'wpbruecke_preview_pre_get_posts' );

/**
 * The main query may only return the one post the token is for.
 *
 * @param WP_Post[] $posts Posts.
 * @param WP_Query  $query Query.
 * @return WP_Post[]
 */
function wpbruecke_preview_the_posts( $posts, $query ) {
	$current = wpbruecke_preview_current();
	if ( ! $current || ! $query->is_main_query() ) {
		return $posts;
	}
	if ( 1 !== count( $posts ) || (int) $posts[0]->ID !== (int) $current->ID ) {
		return array();
	}
	return $posts;
}
add_filter( 'the_posts', 'wpbruecke_preview_the_posts', 10, 2 );

/**
 * Headers for a preview page: never indexed, never cached.
 */
function wpbruecke_preview_headers() {
	if ( ! wpbruecke_preview_current() || headers_sent() ) {
		return;
	}
	nocache_headers();
	header( 'X-Robots-Tag: noindex, nofollow' );
	header( 'Cache-Control: no-store, no-cache, must-revalidate, max-age=0, private' );
}
add_action( 'template_redirect', 'wpbruecke_preview_headers', 1 );
