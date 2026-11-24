<?php
/**
 * SEO title and description fields.
 *
 * _wpbruecke_description and _wpbruecke_title are post meta a drafter can set over REST. They are
 * printed into the page head only when no SEO plugin is active. With Yoast SEO, Rank Math or SEOPress
 * active, their description keys are registered for REST instead, so a drafter can set those.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Post types that get the fields.
 *
 * @return string[]
 */
function wpbruecke_seo_post_types() {
	return array( 'post', 'page' );
}

/**
 * Meta auth: the current user can edit that post.
 *
 * @param bool   $allowed  Unused.
 * @param string $meta_key Unused.
 * @param int    $post_id  Post ID.
 * @return bool
 */
function wpbruecke_meta_auth( $allowed, $meta_key, $post_id ) {
	return current_user_can( 'edit_post', (int) $post_id );
}

/**
 * The description meta key of each supported SEO plugin.
 *
 * @return array<string,string>
 */
function wpbruecke_seo_plugin_keys() {
	return array(
		'yoast'    => '_yoast_wpseo_metadesc',
		'rankmath' => 'rank_math_description',
		'seopress' => '_seopress_titles_desc',
	);
}

/**
 * Register the meta fields.
 */
function wpbruecke_register_meta() {
	$revisions = version_compare( get_bloginfo( 'version' ), '6.4', '>=' );
	$text      = array(
		'type'              => 'string',
		'single'            => true,
		'default'           => '',
		'show_in_rest'      => true,
		'sanitize_callback' => 'sanitize_text_field',
		'auth_callback'     => 'wpbruecke_meta_auth',
	);
	if ( $revisions ) {
		$text['revisions_enabled'] = true;
	}
	$source = array(
		'type'              => 'integer',
		'single'            => true,
		'default'           => 0,
		'show_in_rest'      => true,
		'sanitize_callback' => 'absint',
		'auth_callback'     => 'wpbruecke_meta_auth',
		'description'       => 'The live post a draft copy changes.',
	);
	// Set by the review screen's Apply (the person's click), never over REST: Hermes reads it to tell
	// an applied copy from a discarded one once the copy is in the trash.
	$applied = array(
		'type'          => 'integer',
		'single'        => true,
		'default'       => 0,
		'show_in_rest'  => true,
		'auth_callback' => '__return_false',
		'description'   => 'When the review screen applied this draft copy (Unix time).',
	);
	$seo_plugin = wpbruecke_seo_plugin();
	$seo_key    = null;
	if ( $seo_plugin ) {
		$keys    = wpbruecke_seo_plugin_keys();
		$seo_key = $keys[ $seo_plugin ];
	}
	foreach ( wpbruecke_seo_post_types() as $type ) {
		register_post_meta( $type, '_wpbruecke_description', array_merge( $text, array( 'description' => 'Meta description (WPBrücke).' ) ) );
		register_post_meta( $type, '_wpbruecke_title', array_merge( $text, array( 'description' => 'SEO title (WPBrücke).' ) ) );
		register_post_meta( $type, '_wpbruecke_source', $source );
		register_post_meta( $type, '_wpbruecke_applied', $applied );
		if ( $seo_key && ! registered_meta_key_exists( 'post', $seo_key, $type ) ) {
			$plugin_field = $text;
			unset( $plugin_field['default'], $plugin_field['revisions_enabled'] );
			register_post_meta( $type, $seo_key, $plugin_field );
		}
	}
}
add_action( 'init', 'wpbruecke_register_meta' );

/**
 * The singular post the visitor sees, or null.
 *
 * @return int
 */
function wpbruecke_seo_current_id() {
	if ( ! is_singular() ) {
		return 0;
	}
	return (int) get_queried_object_id();
}

/**
 * Print the description in the head (only without an SEO plugin, only on singular views).
 */
function wpbruecke_seo_head() {
	if ( wpbruecke_seo_plugin() ) {
		return;
	}
	$id = wpbruecke_seo_current_id();
	if ( ! $id ) {
		return;
	}
	$description = trim( (string) get_post_meta( $id, '_wpbruecke_description', true ) );
	if ( '' === $description ) {
		return;
	}
	echo '<meta name="description" content="' . esc_attr( $description ) . '" />' . "\n";
}
add_action( 'wp_head', 'wpbruecke_seo_head', 1 );

/**
 * Use the SEO title as the document title (only without an SEO plugin, only on singular views).
 *
 * @param string $title Title from earlier filters.
 * @return string
 */
function wpbruecke_seo_document_title( $title ) {
	if ( wpbruecke_seo_plugin() ) {
		return $title;
	}
	$id = wpbruecke_seo_current_id();
	if ( ! $id ) {
		return $title;
	}
	$seo_title = trim( (string) get_post_meta( $id, '_wpbruecke_title', true ) );
	return '' === $seo_title ? $title : $seo_title;
}
add_filter( 'pre_get_document_title', 'wpbruecke_seo_document_title', 20 );

/**
 * Meta keys Apply copies from a draft copy onto its live post.
 *
 * @return string[]
 */
function wpbruecke_seo_copy_keys() {
	return array(
		'_wpbruecke_description',
		'_wpbruecke_title',
		'_yoast_wpseo_metadesc',
		'_yoast_wpseo_title',
		'rank_math_description',
		'rank_math_title',
		'_seopress_titles_desc',
		'_seopress_titles_title',
	);
}
