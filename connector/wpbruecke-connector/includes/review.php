<?php
/**
 * The review screen: wp-admin/admin.php?page=wpbruecke-review&post=<id>.
 *
 * It shows a post (or a draft copy beside the live post it changes) and buttons that act on the
 * logged-in person's own rights: Publish, Schedule, Apply copy and Apply brand. Every button is a
 * POST to admin-post.php with a nonce; nothing changes on GET. Hermes only links here.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Register the hidden admin page (no menu entry; the desk and the row links open it).
 */
function wpbruecke_review_menu() {
	add_submenu_page(
		'',
		__( 'WPBrücke review', 'wpbruecke-connector' ),
		__( 'WPBrücke review', 'wpbruecke-connector' ),
		'edit_posts',
		'wpbruecke-review',
		'wpbruecke_review_render'
	);
}
add_action( 'admin_menu', 'wpbruecke_review_menu' );

/**
 * A "Review" row link on drafts and draft copies in the post and page lists.
 *
 * @param string[] $actions Row actions.
 * @param WP_Post  $post    Post.
 * @return string[]
 */
function wpbruecke_review_row_action( $actions, $post ) {
	$is_copy = (int) get_post_meta( $post->ID, '_wpbruecke_source', true ) > 0;
	if ( ( $is_copy || in_array( $post->post_status, wpbruecke_preview_statuses(), true ) ) && current_user_can( 'edit_post', $post->ID ) ) {
		$actions['wpbruecke_review'] = '<a href="' . esc_url( wpbruecke_review_url( $post->ID ) ) . '">' . esc_html__( 'Review', 'wpbruecke-connector' ) . '</a>';
	}
	return $actions;
}
add_filter( 'post_row_actions', 'wpbruecke_review_row_action', 10, 2 );
add_filter( 'page_row_actions', 'wpbruecke_review_row_action', 10, 2 );

/**
 * The verbs of the review screen.
 *
 * @return string[]
 */
function wpbruecke_review_verbs() {
	return array( 'publish', 'schedule', 'apply', 'brand' );
}

/**
 * Nonce action for one verb and post.
 *
 * @param string $verb    Verb.
 * @param int    $post_id Post ID (0 for brand).
 * @return string
 */
function wpbruecke_review_nonce_action( $verb, $post_id ) {
	return 'wpbruecke_review_' . $verb . '_' . (int) $post_id;
}

/**
 * Notices the screen shows after a redirect (code => [type, text]).
 *
 * @return array<string,array>
 */
function wpbruecke_review_notices() {
	return array(
		'published'      => array( 'success', __( 'Published.', 'wpbruecke-connector' ) ),
		'scheduled'      => array( 'success', __( 'Scheduled.', 'wpbruecke-connector' ) ),
		'applied'        => array( 'success', __( 'The copy was applied to the live post (a revision keeps the old version) and the copy moved to the trash.', 'wpbruecke-connector' ) ),
		'brand_applied'  => array( 'success', __( 'The staged colours are now GeneratePress\' global colours.', 'wpbruecke-connector' ) ),
		'denied'         => array( 'error', __( 'Your WordPress user is not allowed to do this.', 'wpbruecke-connector' ) ),
		'bad_date'       => array( 'error', __( 'Pick a date and time in the future.', 'wpbruecke-connector' ) ),
		'not_draft'      => array( 'error', __( 'This post is already published.', 'wpbruecke-connector' ) ),
		'no_source'      => array( 'error', __( 'The live post of this copy no longer exists.', 'wpbruecke-connector' ) ),
		'not_gp'         => array( 'error', __( 'The active theme is not GeneratePress, so the staged brand was not applied.', 'wpbruecke-connector' ) ),
		'nothing_staged' => array( 'error', __( 'There is no staged brand.', 'wpbruecke-connector' ) ),
		'failed'         => array( 'error', __( 'WordPress refused the change.', 'wpbruecke-connector' ) ),
	);
}

/**
 * Redirect back to the screen with a notice code, then stop.
 *
 * @param int    $post_id Post ID.
 * @param string $code    Notice code.
 */
function wpbruecke_review_redirect( $post_id, $code ) {
	wp_safe_redirect( add_query_arg( 'wpb_notice', $code, wpbruecke_review_url( $post_id ) ) );
	exit;
}

/**
 * Post type label for display.
 *
 * @param WP_Post $post Post.
 * @return string
 */
function wpbruecke_review_type_label( $post ) {
	$type = get_post_type_object( $post->post_type );
	return $type ? $type->labels->singular_name : $post->post_type;
}

/**
 * Whether the current user may publish posts of this post's type.
 *
 * @param WP_Post $post Post.
 * @return bool
 */
function wpbruecke_review_can_publish( $post ) {
	$type = get_post_type_object( $post->post_type );
	return $type && current_user_can( $type->cap->publish_posts ) && current_user_can( 'edit_post', $post->ID );
}

/**
 * Open a form to admin-post.php for one verb.
 *
 * @param string $verb    Verb.
 * @param int    $post_id Post ID.
 */
function wpbruecke_review_form_open( $verb, $post_id ) {
	echo '<form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '" style="display:inline-block;margin:0 12px 12px 0;">';
	echo '<input type="hidden" name="action" value="' . esc_attr( 'wpbruecke_review_' . $verb ) . '" />';
	echo '<input type="hidden" name="post" value="' . esc_attr( (string) (int) $post_id ) . '" />';
	wp_nonce_field( wpbruecke_review_nonce_action( $verb, $post_id ) );
}

/**
 * One post's summary rows.
 *
 * @param WP_Post $post Post.
 */
function wpbruecke_review_post_box( $post ) {
	$status = get_post_status_object( $post->post_status );
	echo '<table class="widefat striped" style="max-width:720px"><tbody>';
	echo '<tr><th scope="row">' . esc_html__( 'Title', 'wpbruecke-connector' ) . '</th><td>' . esc_html( get_the_title( $post ) ) . '</td></tr>';
	echo '<tr><th scope="row">' . esc_html__( 'Type', 'wpbruecke-connector' ) . '</th><td>' . esc_html( wpbruecke_review_type_label( $post ) ) . '</td></tr>';
	echo '<tr><th scope="row">' . esc_html__( 'Status', 'wpbruecke-connector' ) . '</th><td>' . esc_html( $status ? $status->label : $post->post_status ) . '</td></tr>';
	echo '<tr><th scope="row">' . esc_html__( 'Last saved', 'wpbruecke-connector' ) . '</th><td>' . esc_html( get_the_modified_date( '', $post ) . ' ' . get_the_modified_time( '', $post ) ) . '</td></tr>';
	echo '<tr><th scope="row">' . esc_html__( 'Open', 'wpbruecke-connector' ) . '</th><td>';
	if ( 'publish' === $post->post_status ) {
		echo '<a href="' . esc_url( get_permalink( $post ) ) . '" target="_blank" rel="noopener">' . esc_html__( 'View live', 'wpbruecke-connector' ) . '</a>';
	} else {
		$link = wpbruecke_preview_link( $post );
		if ( ! is_wp_error( $link ) ) {
			echo '<a href="' . esc_url( $link['url'] ) . '" target="_blank" rel="noopener">' . esc_html__( 'Preview', 'wpbruecke-connector' ) . '</a>';
		}
	}
	if ( current_user_can( 'edit_post', $post->ID ) ) {
		echo ' · <a href="' . esc_url( get_edit_post_link( $post->ID ) ) . '">' . esc_html__( 'Edit', 'wpbruecke-connector' ) . '</a>';
	}
	echo '</td></tr></tbody></table>';
}

/**
 * The staged brand section.
 */
function wpbruecke_review_brand_section() {
	$staged = wpbruecke_staged_brand();
	if ( ! $staged ) {
		return;
	}
	echo '<h2>' . esc_html__( 'Staged brand', 'wpbruecke-connector' ) . '</h2>';
	echo '<p>';
	foreach ( (array) $staged['colors'] as $item ) {
		$color = sanitize_hex_color( isset( $item['color'] ) ? $item['color'] : '' );
		if ( ! $color ) {
			continue;
		}
		echo '<span style="display:inline-block;margin:0 12px 8px 0;"><span style="display:inline-block;width:18px;height:18px;vertical-align:middle;border:1px solid #ccc;background:' . esc_attr( $color ) . '"></span> ';
		echo esc_html( $item['name'] . ' (' . $item['slug'] . ') ' . $color ) . '</span>';
	}
	echo '</p>';
	if ( ! empty( $staged['fonts'] ) ) {
		echo '<p>' . esc_html__( 'Fonts (shown only; apply them in the Customizer):', 'wpbruecke-connector' ) . ' ';
		foreach ( (array) $staged['fonts'] as $key => $font ) {
			echo esc_html( $key . ': ' . $font ) . ' ';
		}
		echo '</p>';
	}
	if ( ! current_user_can( 'edit_theme_options' ) ) {
		echo '<p>' . esc_html__( 'Applying a brand needs a user who can change the theme options.', 'wpbruecke-connector' ) . '</p>';
		return;
	}
	if ( ! wpbruecke_is_generatepress() ) {
		echo '<p>' . esc_html__( 'The active theme is not GeneratePress, so the brand cannot be applied here.', 'wpbruecke-connector' ) . '</p>';
		return;
	}
	wpbruecke_review_form_open( 'brand', 0 );
	submit_button( __( 'Apply brand', 'wpbruecke-connector' ), 'primary', 'submit', false );
	echo '</form>';
}

/**
 * GET: render the screen. Changes nothing.
 */
function wpbruecke_review_render() {
	if ( ! current_user_can( 'edit_posts' ) ) {
		wp_die( esc_html__( 'You are not allowed to review posts.', 'wpbruecke-connector' ), 403 );
	}
	// phpcs:disable WordPress.Security.NonceVerification.Recommended -- read-only screen.
	$post_id = isset( $_GET['post'] ) ? absint( wp_unslash( $_GET['post'] ) ) : 0;
	$notice  = isset( $_GET['wpb_notice'] ) ? sanitize_key( wp_unslash( $_GET['wpb_notice'] ) ) : '';
	// phpcs:enable
	echo '<div class="wrap">';
	echo '<h1>' . esc_html__( 'WPBrücke review', 'wpbruecke-connector' ) . '</h1>';
	$notices = wpbruecke_review_notices();
	if ( isset( $notices[ $notice ] ) ) {
		echo '<div class="notice notice-' . esc_attr( $notices[ $notice ][0] ) . '"><p>' . esc_html( $notices[ $notice ][1] ) . '</p></div>';
	}
	echo '<p>' . esc_html__( 'Every button here acts with your own WordPress user and its rights. Nothing changes until you click.', 'wpbruecke-connector' ) . '</p>';

	$post = $post_id ? get_post( $post_id ) : null;
	if ( $post_id && ( ! $post || ! current_user_can( 'edit_post', $post->ID ) ) ) {
		echo '<p>' . esc_html__( 'This post does not exist or you cannot edit it.', 'wpbruecke-connector' ) . '</p>';
		$post = null;
	}
	if ( $post ) {
		$source_id = (int) get_post_meta( $post->ID, '_wpbruecke_source', true );
		$source    = $source_id ? get_post( $source_id ) : null;
		if ( $source_id && 'trash' !== $post->post_status ) {
			echo '<div style="display:flex;gap:24px;flex-wrap:wrap">';
			echo '<div style="flex:1;min-width:320px"><h2>' . esc_html__( 'Draft copy', 'wpbruecke-connector' ) . '</h2>';
			wpbruecke_review_post_box( $post );
			echo '</div><div style="flex:1;min-width:320px"><h2>' . esc_html__( 'Live post it changes', 'wpbruecke-connector' ) . '</h2>';
			if ( $source && 'trash' !== $source->post_status ) {
				wpbruecke_review_post_box( $source );
			} else {
				echo '<p>' . esc_html__( 'The live post no longer exists.', 'wpbruecke-connector' ) . '</p>';
			}
			echo '</div></div>';
			echo '<h2>' . esc_html__( 'Apply', 'wpbruecke-connector' ) . '</h2>';
			if ( $source && 'trash' !== $source->post_status && current_user_can( 'edit_post', $source->ID ) && current_user_can( 'delete_post', $post->ID ) ) {
				echo '<p>' . esc_html__( 'Apply copies the title, content, excerpt, featured image and SEO fields of the copy onto the live post, keeps the live post\'s status, and moves the copy to the trash.', 'wpbruecke-connector' ) . '</p>';
				wpbruecke_review_form_open( 'apply', $post->ID );
				submit_button( __( 'Apply copy to the live post', 'wpbruecke-connector' ), 'primary', 'submit', false );
				echo '</form>';
			} else {
				echo '<p>' . esc_html__( 'Your user cannot change the live post, so Apply is not offered.', 'wpbruecke-connector' ) . '</p>';
			}
		} else {
			wpbruecke_review_post_box( $post );
			if ( in_array( $post->post_status, wpbruecke_preview_statuses(), true ) ) {
				echo '<h2>' . esc_html__( 'Publish', 'wpbruecke-connector' ) . '</h2>';
				if ( wpbruecke_review_can_publish( $post ) ) {
					wpbruecke_review_form_open( 'publish', $post->ID );
					submit_button( __( 'Publish now', 'wpbruecke-connector' ), 'primary', 'submit', false );
					echo '</form>';
					wpbruecke_review_form_open( 'schedule', $post->ID );
					echo '<label>' . esc_html__( 'Schedule for', 'wpbruecke-connector' ) . ' <input type="datetime-local" name="wpb_when" required /></label> ';
					submit_button( __( 'Schedule', 'wpbruecke-connector' ), 'secondary', 'submit', false );
					echo '</form>';
					echo '<p class="description">' . esc_html( sprintf( /* translators: %s: time zone */ __( 'Times are in the site\'s time zone (%s).', 'wpbruecke-connector' ), wp_timezone_string() ) ) . '</p>';
				} else {
					echo '<p>' . esc_html__( 'Your user cannot publish, so Publish and Schedule are not offered.', 'wpbruecke-connector' ) . '</p>';
				}
			}
		}
	} elseif ( ! $post_id ) {
		echo '<p>' . esc_html__( 'Open this screen from the WPBrücke review desk or from the Review link in the post list.', 'wpbruecke-connector' ) . '</p>';
	}
	wpbruecke_review_brand_section();
	echo '</div>';
}

/**
 * Common start of every POST handler: nonce, then the post.
 *
 * @param string $verb Verb.
 * @return WP_Post|null Null for brand.
 */
function wpbruecke_review_begin( $verb ) {
	$post_id = isset( $_POST['post'] ) ? absint( wp_unslash( $_POST['post'] ) ) : 0; // phpcs:ignore WordPress.Security.NonceVerification.Missing -- checked on the next line.
	check_admin_referer( wpbruecke_review_nonce_action( $verb, $post_id ) );
	if ( 'brand' === $verb ) {
		return null;
	}
	$post = $post_id ? get_post( $post_id ) : null;
	if ( ! $post || ! current_user_can( 'edit_post', $post->ID ) ) {
		wpbruecke_review_redirect( $post_id, 'denied' );
	}
	return $post;
}

/**
 * POST publish: the person's own publish right.
 */
function wpbruecke_review_publish() {
	$post = wpbruecke_review_begin( 'publish' );
	if ( ! wpbruecke_review_can_publish( $post ) ) {
		wpbruecke_review_redirect( $post->ID, 'denied' );
	}
	if ( ! in_array( $post->post_status, wpbruecke_preview_statuses(), true ) ) {
		wpbruecke_review_redirect( $post->ID, 'not_draft' );
	}
	if ( (int) get_post_meta( $post->ID, '_wpbruecke_source', true ) > 0 ) {
		wpbruecke_review_redirect( $post->ID, 'denied' ); // A draft copy is applied, never published as a second post.
	}
	$result = wp_update_post(
		array(
			'ID'            => $post->ID,
			'post_status'   => 'publish',
			'post_date'     => current_time( 'mysql' ),
			'post_date_gmt' => current_time( 'mysql', true ),
			'edit_date'     => true,
		),
		true
	);
	wpbruecke_review_redirect( $post->ID, is_wp_error( $result ) || ! $result ? 'failed' : 'published' );
}
add_action( 'admin_post_wpbruecke_review_publish', 'wpbruecke_review_publish' );

/**
 * POST schedule: a future date in the site's time zone, the person's own publish right.
 */
function wpbruecke_review_schedule() {
	$post = wpbruecke_review_begin( 'schedule' );
	if ( ! wpbruecke_review_can_publish( $post ) ) {
		wpbruecke_review_redirect( $post->ID, 'denied' );
	}
	if ( ! in_array( $post->post_status, wpbruecke_preview_statuses(), true ) ) {
		wpbruecke_review_redirect( $post->ID, 'not_draft' );
	}
	if ( (int) get_post_meta( $post->ID, '_wpbruecke_source', true ) > 0 ) {
		wpbruecke_review_redirect( $post->ID, 'denied' );
	}
	$raw  = isset( $_POST['wpb_when'] ) ? sanitize_text_field( wp_unslash( $_POST['wpb_when'] ) ) : ''; // phpcs:ignore WordPress.Security.NonceVerification.Missing -- checked in wpbruecke_review_begin().
	$when = DateTimeImmutable::createFromFormat( 'Y-m-d\TH:i', $raw, wp_timezone() );
	if ( ! $when || $when->getTimestamp() <= time() + 60 ) {
		wpbruecke_review_redirect( $post->ID, 'bad_date' );
	}
	$local  = $when->format( 'Y-m-d H:i:s' );
	$result = wp_update_post(
		array(
			'ID'            => $post->ID,
			'post_status'   => 'future',
			'post_date'     => $local,
			'post_date_gmt' => get_gmt_from_date( $local ),
			'edit_date'     => true,
		),
		true
	);
	wpbruecke_review_redirect( $post->ID, is_wp_error( $result ) || ! $result ? 'failed' : 'scheduled' );
}
add_action( 'admin_post_wpbruecke_review_schedule', 'wpbruecke_review_schedule' );

/**
 * POST apply: copy a draft copy onto its live post with the person's own rights, then trash the copy.
 */
function wpbruecke_review_apply() {
	$copy      = wpbruecke_review_begin( 'apply' );
	$source_id = (int) get_post_meta( $copy->ID, '_wpbruecke_source', true );
	$source    = $source_id && $source_id !== $copy->ID ? get_post( $source_id ) : null;
	if ( ! $source || 'trash' === $source->post_status || $source->post_type !== $copy->post_type ) {
		wpbruecke_review_redirect( $copy->ID, 'no_source' );
	}
	if ( ! current_user_can( 'edit_post', $source->ID ) || ! current_user_can( 'delete_post', $copy->ID ) ) {
		wpbruecke_review_redirect( $copy->ID, 'denied' );
	}
	// wp_update_post() unslashes its input, so the values go in slashed.
	$result = wp_update_post(
		wp_slash(
			array(
				'ID'           => $source->ID,
				'post_title'   => $copy->post_title,
				'post_content' => $copy->post_content,
				'post_excerpt' => $copy->post_excerpt,
			)
		),
		true
	);
	if ( is_wp_error( $result ) || ! $result ) {
		wpbruecke_review_redirect( $copy->ID, 'failed' );
	}
	$thumbnail = (int) get_post_meta( $copy->ID, '_thumbnail_id', true );
	if ( $thumbnail ) {
		set_post_thumbnail( $source->ID, $thumbnail );
	} else {
		delete_post_thumbnail( $source->ID );
	}
	foreach ( wpbruecke_seo_copy_keys() as $key ) {
		if ( metadata_exists( 'post', $copy->ID, $key ) ) {
			update_post_meta( $source->ID, $key, wp_slash( get_post_meta( $copy->ID, $key, true ) ) );
		}
	}
	update_post_meta( $copy->ID, '_wpbruecke_applied', time() );
	wp_trash_post( $copy->ID );
	wpbruecke_review_redirect( $source->ID, 'applied' );
}
add_action( 'admin_post_wpbruecke_review_apply', 'wpbruecke_review_apply' );

/**
 * POST brand: write the staged colours into GeneratePress (edit_theme_options).
 */
function wpbruecke_review_brand() {
	wpbruecke_review_begin( 'brand' );
	if ( ! current_user_can( 'edit_theme_options' ) ) {
		wpbruecke_review_redirect( 0, 'denied' );
	}
	$result = wpbruecke_apply_staged_brand();
	if ( is_wp_error( $result ) ) {
		$code = 'wpbruecke_not_generatepress' === $result->get_error_code() ? 'not_gp' : 'nothing_staged';
		wpbruecke_review_redirect( 0, $code );
	}
	wpbruecke_review_redirect( 0, 'brand_applied' );
}
add_action( 'admin_post_wpbruecke_review_brand', 'wpbruecke_review_brand' );
