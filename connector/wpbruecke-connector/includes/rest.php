<?php
/**
 * REST routes under wpbruecke/v1.
 *
 * GET  /info          edit_posts: connector facts (reader: the reader role exists); update counts only for
 *                     update_plugins users.
 * POST /preview-link  edit_post on that post: a signed preview link {url, expires_at}.
 * POST /gp/brand      edit_posts: stage a brand for review {staged: true, review_url}.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Logged-in user who can edit posts.
 *
 * @return bool
 */
function wpbruecke_rest_can_edit_posts() {
	return is_user_logged_in() && current_user_can( 'edit_posts' );
}

/**
 * User who can edit the post named in the request.
 *
 * @param WP_REST_Request $request Request.
 * @return bool
 */
function wpbruecke_rest_can_edit_post( $request ) {
	$post_id = absint( $request->get_param( 'post_id' ) );
	return $post_id > 0 && is_user_logged_in() && current_user_can( 'edit_post', $post_id );
}

/**
 * GET /info.
 *
 * @return WP_REST_Response
 */
function wpbruecke_rest_info() {
	return rest_ensure_response(
		array(
			'version'       => WPBRUECKE_CONNECTOR_VERSION,
			'role'          => WPBRUECKE_ROLE,
			'reader'        => (bool) get_role( WPBRUECKE_READER_ROLE ),
			'preview'       => true,
			'seo_field'     => true,
			'seo_plugin'    => wpbruecke_seo_plugin(),
			'review_screen' => true,
			'review_url'    => wpbruecke_review_url(),
			'updates'       => current_user_can( 'update_plugins' ) ? wpbruecke_updates_summary() : null,
		)
	);
}

/**
 * POST /preview-link.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_preview_link( $request ) {
	$result = wpbruecke_preview_link( absint( $request->get_param( 'post_id' ) ) );
	if ( is_wp_error( $result ) ) {
		return $result;
	}
	$response = rest_ensure_response( $result );
	$response->header( 'Cache-Control', 'no-store' );
	return $response;
}

/**
 * POST /gp/brand.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_gp_brand( $request ) {
	$result = wpbruecke_stage_brand(
		array(
			'colors' => $request->get_param( 'colors' ),
			'fonts'  => $request->get_param( 'fonts' ),
		)
	);
	if ( is_wp_error( $result ) ) {
		return $result;
	}
	return rest_ensure_response( $result );
}

/**
 * Register the routes.
 */
function wpbruecke_register_rest_routes() {
	register_rest_route(
		'wpbruecke/v1',
		'/info',
		array(
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'wpbruecke_rest_info',
			'permission_callback' => 'wpbruecke_rest_can_edit_posts',
		)
	);
	register_rest_route(
		'wpbruecke/v1',
		'/preview-link',
		array(
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'wpbruecke_rest_preview_link',
			'permission_callback' => 'wpbruecke_rest_can_edit_post',
			'args'                => array(
				'post_id' => array(
					'type'              => 'integer',
					'required'          => true,
					'minimum'           => 1,
					'sanitize_callback' => 'absint',
				),
			),
		)
	);
	register_rest_route(
		'wpbruecke/v1',
		'/gp/brand',
		array(
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'wpbruecke_rest_gp_brand',
			'permission_callback' => 'wpbruecke_rest_can_edit_posts',
			'args'                => array(
				'colors' => array(
					'type'    => 'array',
					'default' => array(),
					'items'   => array( 'type' => 'object' ),
				),
				'fonts'  => array(
					'type'    => 'object',
					'default' => array(),
				),
			),
		)
	);
}
add_action( 'rest_api_init', 'wpbruecke_register_rest_routes' );
