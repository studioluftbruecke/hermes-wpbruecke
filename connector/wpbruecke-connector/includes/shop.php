<?php
/**
 * WooCommerce (v0.5): two shop roles, a route that saves draft products, and a guard that keeps
 * publishing products to the users WordPress lets publish them.
 *
 * Roles (created on activation and on the first request after an update, removed on deactivation,
 * because the guard below stops with the plugin):
 * - wpbruecke_shop_drafter: Contributor rights plus upload_files, edit_products, read_product,
 *   read_private_products and assign_product_terms. Never publish_products, never other users'
 *   products, never published ones.
 * - wpbruecke_shop_publisher: Author rights plus edit_products, edit_published_products,
 *   read_private_products, assign_product_terms and publish_products. Never other users' products.
 *
 * POST wpbruecke/v1/shop/product  edit_products: create a draft product, or (with id) edit a draft product
 *                                 the caller may edit (edit_product on it), through WooCommerce's product
 *                                 objects. The status is always draft, whatever the request says.
 *                                 WooCommerce's own create checks publish_products, so the drafter role
 *                                 creates its drafts here.
 *
 * The guard: WooCommerce's REST update (wc/v3) checks only edit_product, so without it a user who may
 * edit its own draft product could publish it. Every product save through WooCommerce's objects passes
 * woocommerce_before_product_object_save; a user who holds edit_products but not publish_products is
 * refused there when the save would make a product publish, future or private.
 *
 * The reader role is refused on the POST route by includes/reader.php before any route runs.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_SHOP_DRAFTER_ROLE', 'wpbruecke_shop_drafter' );
define( 'WPBRUECKE_SHOP_PUBLISHER_ROLE', 'wpbruecke_shop_publisher' );
define( 'WPBRUECKE_SHOP_SYNC_OPTION', 'wpbruecke_shop_roles' );

/**
 * Rights neither shop role may hold, whatever the Contributor and Author roles hold on this site.
 *
 * @return string[]
 */
function wpbruecke_shop_never_caps() {
	return array(
		'manage_options',
		'manage_woocommerce',
		'edit_users',
		'list_users',
		'install_plugins',
		'activate_plugins',
		'edit_theme_options',
		'unfiltered_html',
		'edit_others_products',
		'delete_others_products',
		'edit_private_products',
		'delete_private_products',
		'edit_others_posts',
		'edit_others_pages',
		'edit_shop_orders',
		'read_shop_orders',
		'read_private_shop_orders',
		'edit_others_shop_orders',
		'publish_shop_orders',
		'view_woocommerce_reports',
	);
}

/**
 * The two roles' capabilities, built from this site's Contributor and Author roles.
 *
 * @return array<string,array<string,bool>> role slug => caps.
 */
function wpbruecke_shop_role_caps() {
	$contributor = get_role( 'contributor' );
	$author      = get_role( 'author' );
	$drafter     = $contributor && is_array( $contributor->capabilities ) ? $contributor->capabilities : array(
		'read'         => true,
		'edit_posts'   => true,
		'delete_posts' => true,
		'level_1'      => true,
		'level_0'      => true,
	);
	$publisher   = $author && is_array( $author->capabilities ) ? $author->capabilities : array(
		'read'                   => true,
		'edit_posts'             => true,
		'delete_posts'           => true,
		'upload_files'           => true,
		'publish_posts'          => true,
		'edit_published_posts'   => true,
		'delete_published_posts' => true,
		'level_2'                => true,
		'level_1'                => true,
		'level_0'                => true,
	);
	foreach ( array( 'upload_files', 'edit_products', 'read_product', 'read_private_products', 'assign_product_terms' ) as $cap ) {
		$drafter[ $cap ] = true;
	}
	foreach ( array( 'edit_products', 'edit_published_products', 'read_private_products', 'assign_product_terms', 'publish_products' ) as $cap ) {
		$publisher[ $cap ] = true;
	}
	$drafter_never = wpbruecke_shop_drafter_never_caps();
	foreach ( $drafter_never as $cap ) {
		unset( $drafter[ $cap ] );
	}
	foreach ( wpbruecke_shop_never_caps() as $cap ) {
		unset( $publisher[ $cap ] );
	}
	return array(
		WPBRUECKE_SHOP_DRAFTER_ROLE   => $drafter,
		WPBRUECKE_SHOP_PUBLISHER_ROLE => $publisher,
	);
}

/**
 * Rights the drafter role never holds: the shared list, the drafter role's list and publishing products.
 *
 * @return string[]
 */
function wpbruecke_shop_drafter_never_caps() {
	return array_merge(
		wpbruecke_shop_never_caps(),
		wpbruecke_forbidden_caps(),
		array( 'publish_products', 'edit_published_products', 'delete_published_products' )
	);
}

/**
 * Whether a stored role holds a right it must never hold (someone widened it by hand or with a role
 * editor). Checked on every request, so a widened role is narrowed again at once.
 *
 * @return bool
 */
function wpbruecke_shop_roles_widened() {
	$never = array(
		WPBRUECKE_SHOP_DRAFTER_ROLE   => wpbruecke_shop_drafter_never_caps(),
		WPBRUECKE_SHOP_PUBLISHER_ROLE => wpbruecke_shop_never_caps(),
	);
	foreach ( $never as $slug => $caps ) {
		$role = get_role( $slug );
		if ( $role && array_intersect( array_keys( array_filter( (array) $role->capabilities ) ), $caps ) ) {
			return true;
		}
	}
	return false;
}

/**
 * The roles' display names.
 *
 * @return array<string,string>
 */
function wpbruecke_shop_role_labels() {
	return array(
		WPBRUECKE_SHOP_DRAFTER_ROLE   => __( 'WPBrücke shop drafter', 'wpbruecke-connector' ),
		WPBRUECKE_SHOP_PUBLISHER_ROLE => __( 'WPBrücke shop publisher', 'wpbruecke-connector' ),
	);
}

/**
 * Create each role, or bring an existing one back to exactly its rights.
 */
function wpbruecke_shop_sync_roles() {
	$labels = wpbruecke_shop_role_labels();
	foreach ( wpbruecke_shop_role_caps() as $slug => $caps ) {
		$role = get_role( $slug );
		if ( ! $role ) {
			add_role( $slug, $labels[ $slug ], $caps );
			continue;
		}
		foreach ( array_keys( (array) $role->capabilities ) as $cap ) {
			if ( empty( $caps[ $cap ] ) ) {
				$role->remove_cap( $cap );
			}
		}
		foreach ( $caps as $cap => $grant ) {
			if ( $grant && empty( $role->capabilities[ $cap ] ) ) {
				$role->add_cap( $cap );
			}
		}
	}
	update_option( WPBRUECKE_SHOP_SYNC_OPTION, wpbruecke_shop_roles_marker(), true );
}

/**
 * What the synced roles were built from: the connector version and the caps themselves.
 *
 * @return string
 */
function wpbruecke_shop_roles_marker() {
	return WPBRUECKE_CONNECTOR_VERSION . ':' . md5( (string) wp_json_encode( wpbruecke_shop_role_caps() ) );
}

/**
 * Sync the roles when they are missing, were built from other rights (an update, or a plugin that
 * changed the Contributor or Author role), or hold a right they must never hold.
 */
function wpbruecke_shop_maybe_sync() {
	if ( get_option( WPBRUECKE_SHOP_SYNC_OPTION ) !== wpbruecke_shop_roles_marker()
		|| ! get_role( WPBRUECKE_SHOP_DRAFTER_ROLE ) || ! get_role( WPBRUECKE_SHOP_PUBLISHER_ROLE )
		|| wpbruecke_shop_roles_widened() ) {
		wpbruecke_shop_sync_roles();
	}
}
add_action( 'admin_init', 'wpbruecke_shop_maybe_sync' );
add_action( 'rest_api_init', 'wpbruecke_shop_maybe_sync' );

/**
 * Remove both roles on this site: their users keep their accounts and hold no rights from them.
 */
function wpbruecke_shop_remove_roles() {
	remove_role( WPBRUECKE_SHOP_DRAFTER_ROLE );
	remove_role( WPBRUECKE_SHOP_PUBLISHER_ROLE );
	delete_option( WPBRUECKE_SHOP_SYNC_OPTION );
}

/**
 * Activation: the roles on this site (other sites of a network sync on their next request).
 */
function wpbruecke_shop_activate() {
	wpbruecke_shop_sync_roles();
}
register_activation_hook( WPBRUECKE_CONNECTOR_FILE, 'wpbruecke_shop_activate' );

/**
 * Deactivation: the guard stops with the plugin, so the roles go too (on every site when deactivated
 * network-wide).
 *
 * @param bool $network_wide Whether the plugin is deactivated for the whole network.
 */
function wpbruecke_shop_deactivate( $network_wide = false ) {
	if ( $network_wide && is_multisite() ) {
		foreach ( get_sites( array( 'fields' => 'ids' ) ) as $site_id ) {
			switch_to_blog( $site_id );
			wpbruecke_shop_remove_roles();
			restore_current_blog();
		}
		return;
	}
	wpbruecke_shop_remove_roles();
}
register_deactivation_hook( WPBRUECKE_CONNECTOR_FILE, 'wpbruecke_shop_deactivate' );

/* ------------------------------------------------------------------ the publish guard */

/**
 * Statuses that make a product visible or scheduled: only publish_products may set them.
 *
 * @return string[]
 */
function wpbruecke_shop_public_statuses() {
	return array( 'publish', 'future', 'private' );
}

/**
 * The status a save would give the product when it changes it, else null. A new product without a
 * status is saved as publish by WooCommerce's data store.
 *
 * @param WC_Product $product Product about to be saved.
 * @return string|null
 */
function wpbruecke_shop_status_after_save( $product ) {
	$changes = $product->get_changes();
	if ( isset( $changes['status'] ) ) {
		return (string) $changes['status'];
	}
	if ( ! $product->get_id() ) {
		$status = $product->get_status();
		return $status ? (string) $status : 'publish';
	}
	return null;
}

/**
 * Refuse a save that would publish, schedule or privately publish a product for a user who may edit
 * products but not publish them. Saves without a logged-in user (cron, WP-CLI) and users who hold
 * publish_products are left alone, and so is every draft edit.
 *
 * @param WC_Product $product Product about to be saved.
 * @throws WC_Data_Exception When the save would publish for a user without publish_products.
 */
function wpbruecke_shop_publish_guard( $product ) {
	if ( ! is_object( $product ) || ! method_exists( $product, 'get_changes' ) ) {
		return;
	}
	if ( ! is_user_logged_in() || ! current_user_can( 'edit_products' ) || current_user_can( 'publish_products' ) ) {
		return;
	}
	$status = wpbruecke_shop_status_after_save( $product );
	if ( null === $status || ! in_array( $status, wpbruecke_shop_public_statuses(), true ) ) {
		return;
	}
	throw new WC_Data_Exception(
		'wpbruecke_publish_forbidden',
		esc_html__( 'Publishing products needs the publish_products capability; this user saves draft products only.', 'wpbruecke-connector' ),
		403
	);
}
add_action( 'woocommerce_before_product_object_save', 'wpbruecke_shop_publish_guard', 10, 1 );

/* ------------------------------------------------------------------ POST shop/product */

/**
 * Whether WooCommerce is loaded with the parts the route uses.
 *
 * @return bool
 */
function wpbruecke_shop_ready() {
	return class_exists( 'WC_Product_Simple' ) && function_exists( 'wc_get_product' );
}

/**
 * A refusal in the connector's shape.
 *
 * @param string $code    Error code.
 * @param string $message One sentence.
 * @param int    $status  HTTP status.
 * @return WP_Error
 */
function wpbruecke_shop_error( $code, $message, $status ) {
	return new WP_Error( $code, $message, array( 'status' => $status ) );
}

/**
 * Permission: a logged-in user who may edit products. The per-product check is in the route.
 *
 * @return bool
 */
function wpbruecke_shop_can_write() {
	return is_user_logged_in() && current_user_can( 'edit_products' );
}

/**
 * A decimal price, '' to clear it, or null when the value is not a price.
 *
 * @param mixed $value Request value.
 * @return string|null
 */
function wpbruecke_shop_price( $value ) {
	$value = trim( (string) $value );
	if ( '' === $value ) {
		return '';
	}
	return preg_match( '/^\d{1,12}(\.\d{1,6})?$/', $value ) ? $value : null;
}

/**
 * The product the request edits (with id) or a new simple product, or the refusal.
 *
 * @param int $id Product ID or 0.
 * @return WC_Product|WP_Error
 */
function wpbruecke_shop_target( $id ) {
	if ( ! $id ) {
		return new WC_Product_Simple();
	}
	if ( ! current_user_can( 'edit_product', $id ) ) {
		return wpbruecke_shop_error( 'wpbruecke_cannot_edit_product', __( 'This user may not edit that product (it is another user\'s, or published).', 'wpbruecke-connector' ), 403 );
	}
	$product = 'product' === get_post_type( $id ) ? wc_get_product( $id ) : false;
	if ( ! $product ) {
		return wpbruecke_shop_error( 'wpbruecke_not_found', __( 'No such product.', 'wpbruecke-connector' ), 404 );
	}
	if ( 'draft' !== $product->get_status() ) {
		return wpbruecke_shop_error( 'wpbruecke_not_draft', __( 'Only draft products are edited here; a live product changes at the Publish level.', 'wpbruecke-connector' ), 403 );
	}
	if ( ! $product->is_type( 'simple' ) ) {
		return wpbruecke_shop_error( 'wpbruecke_product_type', __( 'This route edits simple products only.', 'wpbruecke-connector' ), 409 );
	}
	return $product;
}

/**
 * Apply the request's fields to the product. Returns a refusal or null.
 *
 * @param WC_Product      $product Product.
 * @param WP_REST_Request $request Request.
 * @return WP_Error|null
 */
function wpbruecke_shop_apply_fields( $product, $request ) {
	$name = $request->get_param( 'name' );
	if ( null !== $name ) {
		$name = sanitize_text_field( (string) $name );
		if ( '' === $name ) {
			return wpbruecke_shop_error( 'wpbruecke_bad_input', __( 'A product name cannot be empty.', 'wpbruecke-connector' ), 400 );
		}
		$product->set_name( $name );
	} elseif ( ! $product->get_id() ) {
		return wpbruecke_shop_error( 'wpbruecke_bad_input', __( 'A new product needs a name.', 'wpbruecke-connector' ), 400 );
	}
	foreach ( array( 'description', 'short_description' ) as $field ) {
		$value = $request->get_param( $field );
		if ( null !== $value ) {
			$setter = 'set_' . $field;
			$product->$setter( wp_kses_post( (string) $value ) );
		}
	}
	foreach ( array( 'regular_price', 'sale_price' ) as $field ) {
		$value = $request->get_param( $field );
		if ( null === $value ) {
			continue;
		}
		$price = wpbruecke_shop_price( $value );
		if ( null === $price ) {
			/* translators: %s: regular_price or sale_price */
			return wpbruecke_shop_error( 'wpbruecke_bad_input', sprintf( __( '%s must be a decimal number such as 19.90, or empty.', 'wpbruecke-connector' ), $field ), 400 );
		}
		$setter = 'set_' . $field;
		$product->$setter( $price );
	}
	$categories = $request->get_param( 'category_ids' );
	if ( null !== $categories ) {
		if ( ! current_user_can( 'assign_product_terms' ) ) {
			return wpbruecke_shop_error( 'wpbruecke_cannot_assign_terms', __( 'This user may not assign product categories.', 'wpbruecke-connector' ), 403 );
		}
		$ids = array();
		foreach ( (array) $categories as $term_id ) {
			$term_id = absint( $term_id );
			if ( ! $term_id || ! term_exists( $term_id, 'product_cat' ) ) {
				return wpbruecke_shop_error( 'wpbruecke_bad_input', __( 'A category id is not a product category on this site.', 'wpbruecke-connector' ), 400 );
			}
			$ids[] = $term_id;
		}
		$product->set_category_ids( $ids );
	}
	$image = $request->get_param( 'image_id' );
	if ( null !== $image ) {
		$image = absint( $image );
		if ( $image && ( ! wp_attachment_is_image( $image ) || ! current_user_can( 'read_post', $image ) ) ) {
			return wpbruecke_shop_error( 'wpbruecke_bad_image', __( 'image_id is not an image this user can read.', 'wpbruecke-connector' ), 400 );
		}
		$product->set_image_id( $image );
	}
	return null;
}

/**
 * What the route answers: the product as saved, read again from WooCommerce.
 *
 * @param int  $id      Product ID.
 * @param bool $created Whether the request created it.
 * @return array
 */
function wpbruecke_shop_product_answer( $id, $created ) {
	$product = wc_get_product( $id );
	$post    = get_post( $id );
	return array(
		'id'                    => (int) $id,
		'created'               => (bool) $created,
		'status'                => $product->get_status(),
		'post_status'           => $post ? $post->post_status : null,
		'author'                => $post ? (int) $post->post_author : 0,
		'type'                  => $product->get_type(),
		'name'                  => $product->get_name(),
		'regular_price'         => $product->get_regular_price(),
		'sale_price'            => $product->get_sale_price(),
		'category_ids'          => array_map( 'intval', (array) $product->get_category_ids() ),
		'image_id'              => (int) $product->get_image_id(),
		'has_description'       => '' !== (string) $product->get_description(),
		'has_short_description' => '' !== (string) $product->get_short_description(),
		'edit_url'              => get_edit_post_link( $id, 'raw' ),
	);
}

/**
 * POST shop/product.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_shop_product( $request ) {
	if ( ! wpbruecke_shop_ready() ) {
		return wpbruecke_shop_error( 'wpbruecke_no_woocommerce', __( 'WooCommerce is not active on this site.', 'wpbruecke-connector' ), 409 );
	}
	$id      = absint( $request->get_param( 'id' ) );
	$product = wpbruecke_shop_target( $id );
	if ( is_wp_error( $product ) ) {
		return $product;
	}
	$refused = wpbruecke_shop_apply_fields( $product, $request );
	if ( $refused ) {
		return $refused;
	}
	$product->set_status( 'draft' );
	try {
		$saved = $product->save();
	} catch ( Exception $e ) {
		return wpbruecke_shop_error( 'wpbruecke_save_failed', $e->getMessage(), 500 );
	}
	if ( ! $saved ) {
		return wpbruecke_shop_error( 'wpbruecke_save_failed', __( 'WooCommerce did not save the product.', 'wpbruecke-connector' ), 500 );
	}
	$response = rest_ensure_response( wpbruecke_shop_product_answer( $saved, ! $id ) );
	$response->set_status( $id ? 200 : 201 );
	return $response;
}

/**
 * Register the route.
 */
function wpbruecke_register_shop_routes() {
	register_rest_route(
		'wpbruecke/v1',
		'/shop/product',
		array(
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'wpbruecke_rest_shop_product',
			'permission_callback' => 'wpbruecke_shop_can_write',
			'args'                => array(
				'id'                => array(
					'type'    => 'integer',
					'minimum' => 0,
				),
				'name'              => array( 'type' => 'string' ),
				'description'       => array( 'type' => 'string' ),
				'short_description' => array( 'type' => 'string' ),
				'regular_price'     => array( 'type' => 'string' ),
				'sale_price'        => array( 'type' => 'string' ),
				'category_ids'      => array(
					'type'  => 'array',
					'items' => array( 'type' => 'integer' ),
				),
				'image_id'          => array(
					'type'    => 'integer',
					'minimum' => 0,
				),
			),
		)
	);
}
add_action( 'rest_api_init', 'wpbruecke_register_shop_routes' );
