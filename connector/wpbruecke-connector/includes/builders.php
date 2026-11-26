<?php
/**
 * REST field wpbruecke_builder on posts and pages: which page builder made the post.
 *
 * Registered for context=edit only, so core answers it only to a request that passed edit_post on that
 * post (WP_REST_Posts_Controller asks it for context=edit); the callback checks edit_post again. The
 * builder meta is read here, server side: most builders keep their layout in meta REST does not show.
 * Answer: {id, evidence[]} with id blocks, classic, elementor, divi, wpbakery, beaver, bricks, oxygen,
 * breakdance or unknown_builder (a builder this file can name by its meta but WPBrücke has no id for).
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Builder meta, strongest first: key => array( builder id, expected value or '' for any non-empty value ).
 *
 * @return array
 */
function wpbruecke_builder_meta_keys() {
	return array(
		'_elementor_edit_mode'   => array( 'elementor', 'builder' ),
		'_et_pb_use_builder'     => array( 'divi', 'on' ),
		'_wpb_vc_js_status'      => array( 'wpbakery', 'true' ),
		'_fl_builder_enabled'    => array( 'beaver', '' ),
		'_bricks_editor_mode'    => array( 'bricks', 'bricks' ),
		'_bricks_page_content_2' => array( 'bricks', '' ),
		'_breakdance_data'       => array( 'breakdance', '' ),
		'_oxygen_data'           => array( 'oxygen', '' ),
		'ct_builder_json'        => array( 'oxygen', '' ),
		'_ct_builder_json'       => array( 'oxygen', '' ),
		'ct_builder_shortcodes'  => array( 'oxygen', '' ),
		'_ct_builder_shortcodes' => array( 'oxygen', '' ),
		'panels_data'            => array( 'unknown_builder', '' ),
		'tcb_editor_enabled'     => array( 'unknown_builder', '' ),
		'brizy_post_uid'         => array( 'unknown_builder', '' ),
		'_themify_builder_settings_json' => array( 'unknown_builder', '' ),
	);
}

/**
 * Whether a stored meta value counts as set.
 *
 * @param mixed  $value Stored value.
 * @param string $want  Expected value, or '' for any non-empty value.
 * @return bool
 */
function wpbruecke_builder_meta_matches( $value, $want ) {
	if ( '' !== $want ) {
		return is_scalar( $value ) && (string) $value === $want;
	}
	if ( is_array( $value ) ) {
		return ! empty( $value );
	}
	$text = strtolower( trim( (string) $value ) );
	return ! in_array( $text, array( '', '0', 'false', 'off', 'no', '[]', '{}' ), true );
}

/**
 * The builder of one post.
 *
 * @param int $post_id Post ID.
 * @return array {id: string, evidence: string[]}
 */
function wpbruecke_builder_of( $post_id ) {
	$post_id = absint( $post_id );
	$found   = '';
	$ev      = array();
	foreach ( wpbruecke_builder_meta_keys() as $key => $rule ) {
		$value = get_post_meta( $post_id, $key, true );
		if ( ! wpbruecke_builder_meta_matches( $value, $rule[1] ) ) {
			continue;
		}
		if ( '' === $found ) {
			$found = $rule[0];
		}
		if ( $rule[0] === $found ) {
			$shown = is_scalar( $value ) && strlen( (string) $value ) <= 24 ? (string) $value : 'set';
			$ev[]  = 'meta ' . $key . '=' . $shown;
		}
	}
	if ( '' !== $found ) {
		return array(
			'id'       => $found,
			'evidence' => $ev,
		);
	}
	$post    = get_post( $post_id );
	$content = $post ? (string) $post->post_content : '';
	$markers = array(
		'<!-- wp:divi/' => 'divi',
		'[et_pb_'       => 'divi',
		'[vc_row'       => 'wpbakery',
	);
	foreach ( $markers as $marker => $id ) {
		if ( false !== strpos( $content, $marker ) ) {
			return array(
				'id'       => $id,
				'evidence' => array( 'marker ' . $marker ),
			);
		}
	}
	if ( '' === trim( $content ) || false !== strpos( $content, '<!-- wp:' ) ) {
		return array(
			'id'       => 'blocks',
			'evidence' => array( '' === trim( $content ) ? 'empty body' : 'block markup' ),
		);
	}
	return array(
		'id'       => 'classic',
		'evidence' => array( 'no block markup and no builder meta' ),
	);
}

/**
 * REST get_callback: the builder, only for someone who may edit the post.
 *
 * @param array $prepared The prepared post (has 'id').
 * @return array|null
 */
function wpbruecke_rest_builder_field( $prepared ) {
	$post_id = isset( $prepared['id'] ) ? absint( $prepared['id'] ) : 0;
	if ( ! $post_id || ! current_user_can( 'edit_post', $post_id ) ) {
		return null;
	}
	return wpbruecke_builder_of( $post_id );
}

/**
 * Register the field on posts and pages.
 */
function wpbruecke_register_builder_field() {
	register_rest_field(
		array( 'post', 'page' ),
		'wpbruecke_builder',
		array(
			'get_callback' => 'wpbruecke_rest_builder_field',
			'schema'       => array(
				'description' => 'The page builder that made this post (WPBrücke connector).',
				'type'        => array( 'object', 'null' ),
				'context'     => array( 'edit' ),
				'readonly'    => true,
				'properties'  => array(
					'id'       => array( 'type' => 'string' ),
					'evidence' => array(
						'type'  => 'array',
						'items' => array( 'type' => 'string' ),
					),
				),
			),
		)
	);
}
add_action( 'rest_api_init', 'wpbruecke_register_builder_field' );
