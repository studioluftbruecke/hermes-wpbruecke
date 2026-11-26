<?php
/**
 * Elementor routes (v0.4): read a page's element tree, stage a draft copy, save edits on a draft or a
 * staged copy through Elementor's own document save, apply a staged copy to its live post.
 *
 * GET  wpbruecke/v1/elementor/<id>         edit_post on <id>: the tree, Elementor's version, flags.
 * POST wpbruecke/v1/elementor/<id>/stage   edit_post on <id> and the type's edit_posts: a draft copy owned
 *                                          by the caller (copy_elementor_meta, caches reset, the live post's
 *                                          fingerprint recorded for apply).
 * POST wpbruecke/v1/elementor/<id>         edit_post on <id>, which must be a draft or pending post: edits
 *                                          ({element_id, set: {path: value}}) merged onto the stored tree,
 *                                          then Document::save( elements ) (never its settings).
 * POST wpbruecke/v1/elementor/<copy>/apply the live type's publish_posts, edit_post on the live post,
 *                                          delete_post on the copy; refused when the live post changed
 *                                          since staging. The review screen's apply calls the same function.
 *
 * Scope (A31): texts, links and images of existing widgets of six types, on posts and pages only (other
 * types answer wpbruecke_type_not_supported). No element is added, removed or
 * moved; every other widget and every V4 atomic element is read-only. A caller without unfiltered_html is
 * refused (kses_would_strip) where WordPress' filter would change content the edit does not touch (D41).
 * The reader role is refused on every POST route by includes/reader.php before any route runs.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_ELEMENTOR_BASE_META', '_wpbruecke_elementor_base' );
define( 'WPBRUECKE_ELEMENTOR_MAX_VALUE', 65536 ); // bytes per edit value; packs/elementor/widgets.py MAX_VALUE_BYTES

/**
 * Whether Elementor is loaded with the parts these routes use.
 *
 * @return bool
 */
function wpbruecke_elementor_ready() {
	return class_exists( '\Elementor\Plugin' ) && isset( \Elementor\Plugin::$instance->documents, \Elementor\Plugin::$instance->db );
}

/**
 * Editable setting paths per widget type. "*" stands for one repeater item's _id. Mirrors
 * packs/elementor/widgets.py EDITABLE (a test compares the two).
 *
 * @return array<string,string[]>
 */
function wpbruecke_elementor_editable() {
	$link = array( 'link.url', 'link.is_external', 'link.nofollow' );
	return array(
		'heading'        => array_merge( array( 'title' ), $link ),
		'text-editor'    => array( 'editor' ),
		'button'         => array_merge( array( 'text' ), $link ),
		'image'          => array_merge( array( 'image.id', 'image.url', 'image.alt', 'caption' ), $link ),
		'icon-list'      => array( 'icon_list.*.text', 'icon_list.*.link.url', 'icon_list.*.link.is_external', 'icon_list.*.link.nofollow' ),
		'call-to-action' => array_merge(
			array( 'title', 'description', 'button', 'ribbon_title', 'bg_image.id', 'bg_image.url', 'bg_image.alt', 'graphic_image.id', 'graphic_image.url', 'graphic_image.alt' ),
			$link
		),
	);
}

/**
 * Whether an element is a V4 atomic element (elType or widgetType "e-...").
 *
 * @param array $element Element data.
 * @return bool
 */
function wpbruecke_elementor_is_atomic( $element ) {
	$el_type = isset( $element['elType'] ) ? (string) $element['elType'] : '';
	$widget  = isset( $element['widgetType'] ) ? (string) $element['widgetType'] : '';
	return 0 === strpos( $el_type, 'e-' ) || 0 === strpos( $widget, 'e-' );
}

/**
 * The editable pattern a concrete path matches, or ''.
 *
 * @param string $widget Widget type.
 * @param string $path   Concrete path, e.g. icon_list.ab12cd3.text.
 * @return string
 */
function wpbruecke_elementor_pattern_for( $widget, $path ) {
	$all = wpbruecke_elementor_editable();
	if ( ! isset( $all[ $widget ] ) ) {
		return '';
	}
	$parts = explode( '.', (string) $path );
	foreach ( $all[ $widget ] as $pattern ) {
		$want = explode( '.', $pattern );
		if ( count( $want ) !== count( $parts ) ) {
			continue;
		}
		$ok = true;
		foreach ( $want as $i => $segment ) {
			if ( '*' === $segment ? ! preg_match( '/^[A-Za-z0-9_-]{1,40}$/', $parts[ $i ] ) : $segment !== $parts[ $i ] ) {
				$ok = false;
				break;
			}
		}
		if ( $ok ) {
			return $pattern;
		}
	}
	return '';
}

/**
 * A link URL WordPress keeps as a link: http(s), mailto, tel, or relative. Anything else is refused.
 *
 * @param string $url URL.
 * @return bool
 */
function wpbruecke_elementor_url_ok( $url ) {
	$url = trim( (string) $url );
	if ( '' === $url || preg_match( '#^(/|\#|\?)#', $url ) ) {
		return true;
	}
	return (bool) preg_match( '#^(https?://|mailto:|tel:)#i', $url );
}

/**
 * An error in the routes' shape.
 *
 * @param string $code    Code.
 * @param string $message One sentence.
 * @param int    $status  HTTP status.
 * @param array  $extra   More data.
 * @return WP_Error
 */
function wpbruecke_elementor_error( $code, $message, $status, $extra = array() ) {
	return new WP_Error( $code, $message, array_merge( array( 'status' => $status ), $extra ) );
}

/**
 * The Elementor document of a post built with Elementor, or a WP_Error.
 *
 * @param int $post_id Post ID.
 * @return \Elementor\Core\Base\Document|WP_Error
 */
function wpbruecke_elementor_document( $post_id ) {
	if ( ! wpbruecke_elementor_ready() ) {
		return wpbruecke_elementor_error( 'wpbruecke_no_elementor', 'Elementor is not active on this site.', 409 );
	}
	$type = (string) get_post_type( $post_id );
	if ( ! in_array( $type, array( 'post', 'page' ), true ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_type_not_supported', 'Only posts and pages are edited here; this is a ' . $type . '.', 409 );
	}
	if ( 'builder' !== get_post_meta( $post_id, '_elementor_edit_mode', true ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_not_elementor', 'This post is not built with Elementor.', 409 );
	}
	$document = \Elementor\Plugin::$instance->documents->get( $post_id, false );
	if ( ! $document ) {
		return wpbruecke_elementor_error( 'wpbruecke_not_elementor', 'Elementor has no document for this post.', 409 );
	}
	return $document;
}

/**
 * Elementor meta that Elementor rewrites while rendering or saving, not a person's change: left out of
 * the fingerprint, so a visitor's page view between stage and apply does not stop the apply.
 *
 * @param string $key Meta key.
 * @return bool
 */
function wpbruecke_elementor_is_cache_key( $key ) {
	return in_array( $key, array( '_elementor_css', '_elementor_element_cache', '_elementor_page_assets', '_elementor_markdown_cache', '_elementor_oembed_cache' ), true )
		|| 0 === strpos( $key, '_elementor_migrations_state' );
}

/**
 * The fingerprint apply compares (R31): what a person's edit of the live post would change, including
 * changes made without a modified bump (an SEO plugin's bulk editor, theme-builder conditions): every
 * meta key apply copies or deletes (the _elementor keys but caches, the SEO fields).
 *
 * @param int $post_id Post ID.
 * @return string
 */
function wpbruecke_elementor_fingerprint( $post_id ) {
	$post = get_post( $post_id );
	if ( ! $post ) {
		return '';
	}
	$settings = get_post_meta( $post_id, '_elementor_page_settings', true );
	$seo_keys = wpbruecke_seo_copy_keys();
	$meta     = array();
	foreach ( get_post_meta( $post_id ) as $key => $values ) {
		if ( ( 0 === strpos( $key, '_elementor' ) && ! wpbruecke_elementor_is_cache_key( $key ) ) || in_array( $key, $seo_keys, true ) ) {
			$meta[ $key ] = $values;
		}
	}
	ksort( $meta );
	$parts = array(
		$post->post_modified_gmt,
		$post->post_status,
		$post->post_title,
		$post->post_excerpt,
		$post->post_content,
		(string) get_post_meta( $post_id, '_elementor_data', true ),
		$settings ? wp_json_encode( $settings ) : '',
		(string) get_post_meta( $post_id, '_wp_page_template', true ),
		(string) get_post_meta( $post_id, '_thumbnail_id', true ),
		wp_json_encode( $meta ),
	);
	return hash( 'sha256', implode( "\x1f", $parts ) );
}

/**
 * Whether WordPress' filter would change anything in $data for the current user (D41). False for a
 * user with unfiltered_html, whose saves are stored as sent.
 *
 * @param mixed $data Tree or value.
 * @return bool
 */
function wpbruecke_elementor_kses_changes( $data ) {
	if ( current_user_can( 'unfiltered_html' ) ) {
		return false;
	}
	return \Elementor\Utils::kses_post_deep( $data ) !== $data;
}

/**
 * Widget or element types in a tree Elementor does not know here: a save would drop those elements.
 *
 * @param array $elements Tree.
 * @return string[]
 */
function wpbruecke_elementor_unknown_types( $elements ) {
	$out = array();
	foreach ( (array) $elements as $element ) {
		if ( ! is_array( $element ) ) {
			$out[] = '(not an element)';
			continue;
		}
		$el_type = isset( $element['elType'] ) ? (string) $element['elType'] : '';
		if ( 'widget' === $el_type ) {
			$widget = isset( $element['widgetType'] ) ? (string) $element['widgetType'] : '';
			if ( '' === $widget || ! \Elementor\Plugin::$instance->widgets_manager->get_widget_types( $widget ) ) {
				$out[] = 'widget ' . $widget;
			}
		} elseif ( '' === $el_type || ! \Elementor\Plugin::$instance->elements_manager->get_element_types( $el_type ) ) {
			$out[] = 'element ' . $el_type;
		}
		if ( ! empty( $element['elements'] ) ) {
			$out = array_merge( $out, wpbruecke_elementor_unknown_types( $element['elements'] ) );
		}
	}
	return array_values( array_unique( $out ) );
}

/**
 * An element's dynamic tags as stored: '' => settings.__dynamic__, '<repeater>.<item _id>.' => that item's
 * __dynamic__. Empty ones are left out, so a missing __dynamic__ and an empty one read the same.
 *
 * @param array $element Element data.
 * @return array<string,mixed>
 */
function wpbruecke_elementor_dynamic_raw( $element ) {
	$settings = isset( $element['settings'] ) && is_array( $element['settings'] ) ? $element['settings'] : array();
	$out      = array();
	if ( ! empty( $settings['__dynamic__'] ) ) {
		$out[''] = $settings['__dynamic__'];
	}
	foreach ( $settings as $key => $value ) {
		if ( '__dynamic__' === $key || ! is_array( $value ) ) {
			continue;
		}
		foreach ( $value as $item ) {
			if ( is_array( $item ) && isset( $item['_id'] ) && ! empty( $item['__dynamic__'] ) ) {
				$out[ $key . '.' . $item['_id'] . '.' ] = $item['__dynamic__'];
			}
		}
	}
	ksort( $out );
	return $out;
}

/**
 * Whether the running Elementor resolves a dynamic tag's text. Elementor's save drops every tag it cannot
 * resolve (Controls_Stack::sanitize_settings), e.g. a Pro tag while Pro is off.
 *
 * @param mixed $tag_text The stored tag text.
 * @return bool
 */
function wpbruecke_elementor_tag_known( $tag_text ) {
	$tags = isset( \Elementor\Plugin::$instance->dynamic_tags ) ? \Elementor\Plugin::$instance->dynamic_tags : null;
	if ( ! $tags || ! is_string( $tag_text ) ) {
		return false;
	}
	$data = $tags->tag_text_to_tag_data( $tag_text );
	return is_array( $data ) && ! empty( $data['name'] ) && (bool) $tags->get_tag_info( $data['name'] );
}

/**
 * Dynamic tags in a tree the running Elementor cannot resolve: a save would drop them.
 *
 * @param array $elements Tree.
 * @return string[] "<element id> <setting path>" per tag.
 */
function wpbruecke_elementor_unknown_dynamic( $elements ) {
	$out = array();
	foreach ( (array) $elements as $element ) {
		if ( ! is_array( $element ) ) {
			continue;
		}
		$eid = isset( $element['id'] ) ? (string) $element['id'] : '';
		foreach ( wpbruecke_elementor_dynamic_raw( $element ) as $where => $dynamic ) {
			if ( ! is_array( $dynamic ) ) {
				$out[] = $eid . ' ' . $where . '__dynamic__';
				continue;
			}
			foreach ( $dynamic as $setting => $tag_text ) {
				if ( ! wpbruecke_elementor_tag_known( $tag_text ) ) {
					$out[] = $eid . ' ' . $where . $setting;
				}
			}
		}
		if ( ! empty( $element['elements'] ) ) {
			$out = array_merge( $out, wpbruecke_elementor_unknown_dynamic( $element['elements'] ) );
		}
	}
	return array_values( array_unique( $out ) );
}

/**
 * The refusal for a tree with dynamic tags the running Elementor cannot resolve, or null.
 *
 * @param array $elements Tree.
 * @return WP_Error|null
 */
function wpbruecke_elementor_refuse_unknown_dynamic( $elements ) {
	$unknown = wpbruecke_elementor_unknown_dynamic( $elements );
	if ( ! $unknown ) {
		return null;
	}
	return wpbruecke_elementor_error(
		'wpbruecke_unknown_dynamic',
		'Elementor here cannot resolve the dynamic tags of ' . implode( ', ', $unknown ) . ' (their plugin, e.g. Elementor Pro, is not active), and its save would delete them: a page with dynamic tags from a plugin that is not active cannot be edited here. Edit it in Elementor with that plugin active.',
		409,
		array( 'elements' => $unknown )
	);
}

/**
 * An element's dynamic tags in a comparable form (keys sorted, values as JSON).
 *
 * @param array $element Element data.
 * @return array
 */
function wpbruecke_elementor_dynamic_sets( $element ) {
	$out = array();
	foreach ( wpbruecke_elementor_dynamic_raw( $element ) as $where => $dynamic ) {
		if ( is_array( $dynamic ) ) {
			ksort( $dynamic );
			$out[ $where ] = array_map( 'wp_json_encode', $dynamic );
		} else {
			$out[ $where ] = wp_json_encode( $dynamic );
		}
	}
	return $out;
}

/**
 * The tree's layout: id, type, dynamic tags and nesting of every element, in order. A save that changes it
 * changed something no edit may touch.
 *
 * @param array $elements Tree.
 * @return array
 */
function wpbruecke_elementor_skeleton( $elements ) {
	$out = array();
	foreach ( (array) $elements as $element ) {
		$out[] = array(
			isset( $element['id'] ) ? (string) $element['id'] : '',
			isset( $element['elType'] ) ? (string) $element['elType'] : '',
			isset( $element['widgetType'] ) ? (string) $element['widgetType'] : '',
			is_array( $element ) ? wpbruecke_elementor_dynamic_sets( $element ) : array(),
			wpbruecke_elementor_skeleton( isset( $element['elements'] ) ? $element['elements'] : array() ),
		);
	}
	return $out;
}

/**
 * Normalise one edit value for its path, or return a WP_Error.
 *
 * @param string $pattern Editable pattern.
 * @param mixed  $value   Value sent.
 * @return mixed|WP_Error
 */
function wpbruecke_elementor_value( $pattern, $value ) {
	$leaf = substr( $pattern, strrpos( '.' . $pattern, '.' ) );
	if ( 'is_external' === $leaf || 'nofollow' === $leaf ) {
		return ( true === $value || 'on' === $value || 1 === $value || '1' === $value ) ? 'on' : '';
	}
	if ( 'id' === $leaf ) {
		$id = is_numeric( $value ) ? (int) $value : -1;
		if ( 0 === $id ) {
			return '';
		}
		if ( $id < 0 || 'attachment' !== get_post_type( $id ) || ! wp_attachment_is_image( $id ) ) {
			return wpbruecke_elementor_error( 'wpbruecke_bad_value', 'The image id is not an image in the media library.', 400 );
		}
		return $id;
	}
	if ( ! is_string( $value ) && ! is_int( $value ) && ! is_float( $value ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_bad_value', 'Each value is text (links and image URLs too).', 400 );
	}
	$value = (string) $value;
	if ( strlen( $value ) > WPBRUECKE_ELEMENTOR_MAX_VALUE ) {
		return wpbruecke_elementor_error( 'wpbruecke_bad_value', 'A value is ' . strlen( $value ) . ' bytes; one edit value holds at most 64 KB.', 400 );
	}
	if ( 'url' === $leaf ) {
		if ( ! wpbruecke_elementor_url_ok( $value ) ) {
			return wpbruecke_elementor_error( 'wpbruecke_bad_value', 'A link or image URL is http(s), mailto:, tel: or relative.', 400 );
		}
		return '' === trim( $value ) ? '' : esc_url_raw( trim( $value ) );
	}
	return current_user_can( 'unfiltered_html' ) ? $value : wp_kses_post( $value );
}

/**
 * Set $value at $parts inside $settings, creating no repeater item: an item is found by its _id.
 *
 * @param array    $settings Settings.
 * @param string[] $parts    Path segments.
 * @param mixed    $value    Value.
 * @return array|WP_Error
 */
function wpbruecke_elementor_set_path( $settings, $parts, $value ) {
	$key = array_shift( $parts );
	if ( ! $parts ) {
		$settings[ $key ] = $value;
		return $settings;
	}
	$inner = isset( $settings[ $key ] ) && is_array( $settings[ $key ] ) ? $settings[ $key ] : array();
	if ( 'icon_list' === $key ) {
		$item_id = array_shift( $parts );
		foreach ( $inner as $i => $item ) {
			if ( is_array( $item ) && isset( $item['_id'] ) && (string) $item['_id'] === $item_id ) {
				$dynamic = isset( $item['__dynamic__'] ) && is_array( $item['__dynamic__'] ) ? $item['__dynamic__'] : array();
				if ( isset( $dynamic[ $parts[0] ] ) ) {
					return wpbruecke_elementor_error( 'wpbruecke_dynamic', 'That list item\'s ' . $parts[0] . ' comes from a dynamic tag; it is read-only.', 409 );
				}
				$set = wpbruecke_elementor_set_path( $item, $parts, $value );
				if ( is_wp_error( $set ) ) {
					return $set;
				}
				$inner[ $i ]      = $set;
				$settings[ $key ] = $inner;
				return $settings;
			}
		}
		return wpbruecke_elementor_error( 'wpbruecke_unknown_item', 'The list has no item ' . $item_id . '; items are never added.', 409 );
	}
	$set = wpbruecke_elementor_set_path( $inner, $parts, $value );
	if ( is_wp_error( $set ) ) {
		return $set;
	}
	$settings[ $key ] = $set;
	return $settings;
}

/**
 * An element of a tree by id, or null.
 *
 * @param array  $elements Tree.
 * @param string $eid      Element id.
 * @return array|null
 */
function wpbruecke_elementor_find( $elements, $eid ) {
	foreach ( (array) $elements as $element ) {
		if ( ! is_array( $element ) ) {
			continue;
		}
		if ( isset( $element['id'] ) && (string) $element['id'] === $eid ) {
			return $element;
		}
		$found = wpbruecke_elementor_find( isset( $element['elements'] ) ? $element['elements'] : array(), $eid );
		if ( $found ) {
			return $found;
		}
	}
	return null;
}

/**
 * The value at a path inside settings, a repeater item found by its _id; null when it is not there.
 *
 * @param array    $settings Settings.
 * @param string[] $parts    Path segments.
 * @return mixed
 */
function wpbruecke_elementor_get_path( $settings, $parts ) {
	$cur = $settings;
	while ( $parts ) {
		$key = array_shift( $parts );
		if ( ! is_array( $cur ) ) {
			return null;
		}
		$item = null;
		foreach ( $cur as $row ) {
			if ( is_array( $row ) && isset( $row['_id'] ) && (string) $row['_id'] === $key ) {
				$item = $row;
				break;
			}
		}
		if ( null === $item && ! array_key_exists( $key, $cur ) ) {
			return null;
		}
		$cur = null === $item ? $cur[ $key ] : $item;
	}
	return $cur;
}

/**
 * Apply the edits of one element to its settings.
 *
 * @param array $element Element.
 * @param array $set     Path => value.
 * @param array $stored  Out: path => value as stored.
 * @return array|WP_Error The element.
 */
function wpbruecke_elementor_edit_element( $element, $set, &$stored ) {
	$eid = (string) $element['id'];
	if ( wpbruecke_elementor_is_atomic( $element ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_read_only', 'Element ' . $eid . ' is a V4 atomic element; those are read-only.', 409 );
	}
	$widget   = isset( $element['widgetType'] ) ? (string) $element['widgetType'] : '';
	$editable = wpbruecke_elementor_editable();
	if ( 'widget' !== ( isset( $element['elType'] ) ? $element['elType'] : '' ) || ! array_key_exists( $widget, $editable ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_read_only', 'Element ' . $eid . ' (' . ( $widget ? $widget : $element['elType'] ) . ') is read-only: only heading, text-editor, button, image, icon-list and call-to-action widgets are edited.', 409 );
	}
	$settings = isset( $element['settings'] ) && is_array( $element['settings'] ) ? $element['settings'] : array();
	$dynamic  = isset( $settings['__dynamic__'] ) && is_array( $settings['__dynamic__'] ) ? $settings['__dynamic__'] : array();
	foreach ( $set as $path => $value ) {
		$path    = (string) $path;
		$pattern = wpbruecke_elementor_pattern_for( $widget, $path );
		if ( '' === $pattern ) {
			return wpbruecke_elementor_error( 'wpbruecke_read_only', 'Setting ' . $path . ' of ' . $widget . ' ' . $eid . ' is not editable.', 409 );
		}
		$top = strtok( $path, '.' );
		if ( isset( $dynamic[ $top ] ) ) {
			return wpbruecke_elementor_error( 'wpbruecke_dynamic', 'Setting ' . $top . ' of ' . $eid . ' comes from a dynamic tag; it is read-only.', 409 );
		}
		$clean = wpbruecke_elementor_value( $pattern, $value );
		if ( is_wp_error( $clean ) ) {
			return $clean;
		}
		$settings = wpbruecke_elementor_set_path( $settings, explode( '.', $path ), $clean );
		if ( is_wp_error( $settings ) ) {
			return $settings;
		}
		$stored[ $path ] = $clean;
	}
	foreach ( array( 'image', 'bg_image', 'graphic_image' ) as $media ) {
		$id_set  = array_key_exists( $media . '.id', $set );
		$url_set = array_key_exists( $media . '.url', $set );
		if ( $id_set && '' !== $stored[ $media . '.id' ] ) {
			$settings[ $media ]['url'] = (string) wp_get_attachment_url( $stored[ $media . '.id' ] );
			$stored[ $media . '.url' ] = $settings[ $media ]['url'];
		} elseif ( $url_set && ! $id_set ) {
			$settings[ $media ]['id'] = '';
			$stored[ $media . '.id' ]  = '';
		}
		if ( array_key_exists( $media . '.alt', $set ) && ! empty( $settings[ $media ]['id'] ) ) {
			return wpbruecke_elementor_error( 'wpbruecke_alt_on_library_image', 'A media library image shows its own alt text from the media library; set image.url to an outside image to give it an alt here.', 409 );
		}
	}
	$element['settings'] = $settings;
	return $element;
}

/**
 * Merge edits (element id => path => value) onto a tree.
 *
 * @param array $elements Tree.
 * @param array $edits    Element id => set.
 * @param array $stored   Out: element id => path => stored value.
 * @return array|WP_Error
 */
function wpbruecke_elementor_merge( $elements, $edits, &$stored ) {
	foreach ( $elements as $i => $element ) {
		if ( ! is_array( $element ) ) {
			continue;
		}
		$eid = isset( $element['id'] ) ? (string) $element['id'] : '';
		if ( '' !== $eid && isset( $edits[ $eid ] ) ) {
			$stored[ $eid ] = array();
			$element        = wpbruecke_elementor_edit_element( $element, $edits[ $eid ], $stored[ $eid ] );
			if ( is_wp_error( $element ) ) {
				return $element;
			}
		}
		if ( ! empty( $element['elements'] ) && is_array( $element['elements'] ) ) {
			$children = wpbruecke_elementor_merge( $element['elements'], $edits, $stored );
			if ( is_wp_error( $children ) ) {
				return $children;
			}
			$element['elements'] = $children;
		}
		$elements[ $i ] = $element;
	}
	return $elements;
}

/**
 * The read shape of one post.
 *
 * @param WP_Post $post Post.
 * @return array|WP_Error
 */
function wpbruecke_elementor_read( $post ) {
	$document = wpbruecke_elementor_document( $post->ID );
	if ( is_wp_error( $document ) ) {
		return $document;
	}
	$elements = $document->get_elements_data();
	$settings = get_post_meta( $post->ID, '_elementor_page_settings', true );
	return array(
		'post_id'          => $post->ID,
		'type'             => $post->post_type,
		'status'           => $post->post_status,
		'title'            => $post->post_title,
		'modified_gmt'     => $post->post_modified_gmt,
		'source'           => (int) get_post_meta( $post->ID, '_wpbruecke_source', true ),
		'staged'           => (bool) get_post_meta( $post->ID, WPBRUECKE_ELEMENTOR_BASE_META, true ),
		'version'          => defined( 'ELEMENTOR_VERSION' ) ? ELEMENTOR_VERSION : '',
		'edit_mode'        => 'builder',
		'template_type'    => (string) get_post_meta( $post->ID, '_elementor_template_type', true ),
		'template'         => (string) get_post_meta( $post->ID, '_wp_page_template', true ),
		'elements'         => is_array( $elements ) ? $elements : array(),
		'page_settings'    => is_array( $settings ) ? $settings : array(),
		'unknown_types'    => wpbruecke_elementor_unknown_types( $elements ),
		'unknown_dynamic'  => wpbruecke_elementor_unknown_dynamic( $elements ),
		'unfiltered_html'  => current_user_can( 'unfiltered_html' ),
		'kses_would_strip' => wpbruecke_elementor_kses_changes( array( 'elements' => $elements ) ),
	);
}

/**
 * Stage: a draft copy of an Elementor post, owned by the caller.
 *
 * @param WP_Post $source Live post.
 * @return array|WP_Error
 */
function wpbruecke_elementor_stage( $source ) {
	$document = wpbruecke_elementor_document( $source->ID );
	if ( is_wp_error( $document ) ) {
		return $document;
	}
	if ( (int) get_post_meta( $source->ID, '_wpbruecke_source', true ) > 0 ) {
		return wpbruecke_elementor_error( 'wpbruecke_already_a_copy', 'This post is itself a staged copy; edit it directly.', 409 );
	}
	if ( 'trash' === $source->post_status ) {
		return wpbruecke_elementor_error( 'wpbruecke_trashed', 'This post is in the trash.', 409 );
	}
	$refused = wpbruecke_elementor_refuse_unknown_dynamic( $document->get_elements_data() );
	if ( $refused ) {
		return $refused;
	}
	// copy_elementor_meta writes through update_metadata, whose registered sanitize_callback filters the
	// whole tree for this caller: refuse rather than stage a copy that has lost the admin's embeds.
	if ( wpbruecke_elementor_kses_changes( array( 'elements' => $document->get_elements_data() ) ) ) {
		return wpbruecke_elementor_error( 'kses_would_strip', 'WordPress would strip HTML from this page for your user (no unfiltered_html): a copy would lose parts you did not edit. A user with unfiltered_html stages and edits this page.', 409 );
	}
	try {
		$copy = wpbruecke_elementor_make_copy( $source );
	} catch ( \Throwable $e ) {
		return wpbruecke_elementor_error( 'wpbruecke_elementor_error', 'Elementor failed while copying this page (' . get_class( $e ) . '); no copy was made. Look at the page in Elementor.', 409 );
	}
	if ( is_wp_error( $copy ) ) {
		return $copy;
	}
	return array(
		'copy'     => (int) $copy,
		'source'   => $source->ID,
		'status'   => get_post_status( $copy ),
		'template' => (string) get_post_meta( $copy, '_wp_page_template', true ),
	);
}

/**
 * The stage's draft copy: post, Elementor meta, caches reset, source, fingerprint, SEO fields. A failure
 * part way deletes the half-made copy and rethrows.
 *
 * @param WP_Post $source Live post.
 * @return int|WP_Error The copy's id.
 */
function wpbruecke_elementor_make_copy( $source ) {
	$copy = wp_insert_post(
		wp_slash(
			array(
				'post_type'    => $source->post_type,
				'post_status'  => 'draft',
				'post_title'   => $source->post_title,
				'post_content' => $source->post_content,
				'post_excerpt' => $source->post_excerpt,
				'post_parent'  => $source->post_parent,
				'menu_order'   => $source->menu_order,
				'post_author'  => get_current_user_id(),
			)
		),
		true
	);
	if ( is_wp_error( $copy ) ) {
		return $copy;
	}
	try {
		\Elementor\Plugin::$instance->db->copy_elementor_meta( $source->ID, $copy );
		// copy_elementor_meta also copies the live post's CSS meta and element cache; the copy would link a
		// post-<copy>.css that was never written (P39).
		delete_post_meta( $copy, '_elementor_element_cache' );
		\Elementor\Core\Files\CSS\Post::create( $copy )->delete();
		update_post_meta( $copy, '_wpbruecke_source', $source->ID );
		update_post_meta(
			$copy,
			WPBRUECKE_ELEMENTOR_BASE_META,
			array(
				'fingerprint'  => wpbruecke_elementor_fingerprint( $source->ID ),
				'modified_gmt' => $source->post_modified_gmt,
				'staged_at'    => time(),
			)
		);
		foreach ( wpbruecke_seo_copy_keys() as $key ) {
			if ( metadata_exists( 'post', $source->ID, $key ) ) {
				update_post_meta( $copy, $key, wp_slash( get_post_meta( $source->ID, $key, true ) ) );
			}
		}
	} catch ( \Throwable $e ) {
		wp_delete_post( $copy, true );
		throw $e;
	}
	return (int) $copy;
}

/**
 * Save: merge edits onto a draft's tree and save it through Elementor's document save.
 *
 * @param WP_Post $post  Draft or staged copy.
 * @param array   $edits List of {element_id, set}.
 * @return array|WP_Error
 */
function wpbruecke_elementor_save( $post, $edits ) {
	if ( ! in_array( $post->post_status, array( 'draft', 'pending' ), true ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_live_post', 'Only a draft or a staged copy is saved here; stage a published post first.', 409 );
	}
	$document = wpbruecke_elementor_document( $post->ID );
	if ( is_wp_error( $document ) ) {
		return $document;
	}
	$by_id = array();
	foreach ( (array) $edits as $edit ) {
		if ( ! is_array( $edit ) || ! isset( $edit['element_id'], $edit['set'] ) || ! is_array( $edit['set'] ) || ! $edit['set'] ) {
			return wpbruecke_elementor_error( 'wpbruecke_bad_edit', 'Each edit is {element_id, set: {path: value}} with at least one path.', 400 );
		}
		$eid = (string) $edit['element_id'];
		if ( isset( $by_id[ $eid ] ) ) {
			return wpbruecke_elementor_error( 'wpbruecke_bad_edit', 'Element ' . $eid . ' is named twice; put its paths in one edit.', 400 );
		}
		$by_id[ $eid ] = $edit['set'];
	}
	if ( ! $by_id ) {
		return wpbruecke_elementor_error( 'wpbruecke_bad_edit', 'No edits.', 400 );
	}
	$before  = $document->get_elements_data();
	$unknown = wpbruecke_elementor_unknown_types( $before );
	if ( $unknown ) {
		return wpbruecke_elementor_error( 'wpbruecke_unknown_widget', 'Elementor here does not know ' . implode( ', ', $unknown ) . '; a save would drop those elements.', 409 );
	}
	$refused = wpbruecke_elementor_refuse_unknown_dynamic( $before );
	if ( $refused ) {
		return $refused;
	}
	$stored = array();
	$tree   = wpbruecke_elementor_merge( $before, $by_id, $stored );
	if ( is_wp_error( $tree ) ) {
		return $tree;
	}
	$missing = array_diff( array_keys( $by_id ), array_map( 'strval', array_keys( $stored ) ) );
	if ( $missing ) {
		return wpbruecke_elementor_error( 'wpbruecke_unknown_element', 'No element ' . implode( ', ', $missing ) . ' on this post; elements are never added.', 409 );
	}
	if ( wpbruecke_elementor_kses_changes( array( 'elements' => $tree ) ) ) {
		return wpbruecke_elementor_error( 'kses_would_strip', 'WordPress would strip HTML elsewhere on this page for your user (no unfiltered_html): the save would change parts you did not edit. A user with unfiltered_html makes this edit.', 409 );
	}
	$raw_before = (string) get_post_meta( $post->ID, '_elementor_data', true );
	try {
		$saved = $document->save( array( 'elements' => $tree ) );
		if ( ! $saved ) {
			return wpbruecke_elementor_error( 'wpbruecke_not_saved', 'Elementor did not save the document for this user.', 403 );
		}
		$after = \Elementor\Plugin::$instance->documents->get( $post->ID, false );
		$read  = $after ? $after->get_elements_data() : array();
	} catch ( \Throwable $e ) {
		wpbruecke_elementor_put_back( $post->ID, $raw_before );
		return wpbruecke_elementor_error( 'wpbruecke_elementor_error', 'Elementor failed while saving this page (' . get_class( $e ) . '); the draft was left as it was. Look at it in Elementor.', 409 );
	}
	if ( wpbruecke_elementor_skeleton( $read ) !== wpbruecke_elementor_skeleton( $before ) ) {
		wpbruecke_elementor_put_back( $post->ID, $raw_before );
		return wpbruecke_elementor_error( 'wpbruecke_structure_changed', 'Elementor\'s save changed elements or dynamic tags the edit did not touch; the draft\'s layout was put back as it was before this save.', 409 );
	}
	// What Elementor kept, read from the saved document: its own sanitising may differ from what was sent.
	foreach ( $stored as $eid => $paths ) {
		$element = wpbruecke_elementor_find( $read, (string) $eid );
		$saved   = $element && isset( $element['settings'] ) && is_array( $element['settings'] ) ? $element['settings'] : array();
		foreach ( array_keys( $paths ) as $path ) {
			$stored[ $eid ][ $path ] = wpbruecke_elementor_get_path( $saved, explode( '.', (string) $path ) );
		}
	}
	return array(
		'saved'    => true,
		'post_id'  => $post->ID,
		'stored'   => $stored,
		'elements' => $read,
	);
}

/**
 * Put a draft's stored tree back after a save that went wrong, and drop the caches built from it.
 *
 * @param int    $post_id Post ID.
 * @param string $raw     The _elementor_data meta as it was before the save.
 */
function wpbruecke_elementor_put_back( $post_id, $raw ) {
	if ( (string) get_post_meta( $post_id, '_elementor_data', true ) !== $raw ) {
		update_post_meta( $post_id, '_elementor_data', wp_slash( $raw ) );
	}
	delete_post_meta( $post_id, '_elementor_element_cache' );
	\Elementor\Core\Files\CSS\Post::create( $post_id )->delete();
}

/**
 * Whether the current user may apply this copy to that live post (the route table's apply check).
 *
 * @param WP_Post $copy Copy.
 * @param WP_Post $live Live post.
 * @return bool
 */
function wpbruecke_elementor_can_apply( $copy, $live ) {
	$type = get_post_type_object( $live->post_type );
	return $type && is_user_logged_in()
		&& current_user_can( $type->cap->publish_posts )
		&& current_user_can( 'edit_post', $live->ID )
		&& current_user_can( 'delete_post', $copy->ID );
}

/**
 * Apply a staged Elementor copy to its live post: Elementor's meta first (so the revision carries the new
 * tree), then title, excerpt and content, SEO fields, the live caches, then the copy goes to the trash.
 * The agent's apply route and the review screen's button both call this.
 *
 * @param WP_Post $copy Staged copy.
 * @return array|WP_Error
 */
function wpbruecke_elementor_apply_copy( $copy ) {
	$source_id = (int) get_post_meta( $copy->ID, '_wpbruecke_source', true );
	$live      = $source_id && $source_id !== $copy->ID ? get_post( $source_id ) : null;
	if ( ! $live || 'trash' === $live->post_status || $live->post_type !== $copy->post_type ) {
		return wpbruecke_elementor_error( 'wpbruecke_no_source', 'The live post of this copy no longer exists.', 409 );
	}
	if ( ! wpbruecke_elementor_can_apply( $copy, $live ) ) {
		return wpbruecke_elementor_error( 'rest_forbidden', 'Your user may not publish this post type, edit the live post or delete the copy.', 403 );
	}
	$base = get_post_meta( $copy->ID, WPBRUECKE_ELEMENTOR_BASE_META, true );
	if ( ! is_array( $base ) || empty( $base['fingerprint'] ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_not_elementor_copy', 'This copy was not staged through the Elementor route; apply it in Elementor.', 409 );
	}
	if ( ! in_array( $copy->post_status, array( 'draft', 'pending' ), true ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_not_staged', 'The copy is not a draft.', 409 );
	}
	$copy_doc = wpbruecke_elementor_document( $copy->ID );
	if ( is_wp_error( $copy_doc ) ) {
		return $copy_doc;
	}
	if ( 'builder' !== get_post_meta( $live->ID, '_elementor_edit_mode', true ) ) {
		return wpbruecke_elementor_error( 'wpbruecke_not_elementor', 'The live post is no longer built with Elementor.', 409 );
	}
	if ( ! hash_equals( (string) $base['fingerprint'], wpbruecke_elementor_fingerprint( $live->ID ) ) ) {
		return wpbruecke_elementor_error( 'live_changed', 'The live post changed since this copy was staged; stage it again so nobody\'s change is lost.', 409, array( 'staged_modified_gmt' => isset( $base['modified_gmt'] ) ? $base['modified_gmt'] : null, 'live_modified_gmt' => $live->post_modified_gmt ) );
	}
	if ( wpbruecke_elementor_kses_changes( array( 'elements' => $copy_doc->get_elements_data() ) ) ) {
		return wpbruecke_elementor_error( 'kses_would_strip', 'WordPress would strip HTML from this page for your user (no unfiltered_html); a user with unfiltered_html applies it.', 409 );
	}
	$copy_keys = array();
	foreach ( array_keys( get_post_meta( $copy->ID ) ) as $key ) {
		if ( 0 === strpos( $key, '_elementor' ) ) {
			$copy_keys[] = $key;
		}
	}
	$stale = array();
	foreach ( array_keys( get_post_meta( $live->ID ) ) as $key ) {
		if ( 0 === strpos( $key, '_elementor' ) && ! in_array( $key, $copy_keys, true ) ) {
			$stale[] = $key;
		}
	}
	\Elementor\Plugin::$instance->db->copy_elementor_meta( $copy->ID, $live->ID );
	foreach ( $stale as $key ) {
		delete_post_meta( $live->ID, $key );
	}
	$result = wp_update_post(
		wp_slash(
			array(
				'ID'           => $live->ID,
				'post_title'   => $copy->post_title,
				'post_content' => $copy->post_content,
				'post_excerpt' => $copy->post_excerpt,
			)
		),
		true
	);
	if ( is_wp_error( $result ) ) {
		return $result;
	}
	foreach ( wpbruecke_seo_copy_keys() as $key ) {
		if ( metadata_exists( 'post', $copy->ID, $key ) ) {
			update_post_meta( $live->ID, $key, wp_slash( get_post_meta( $copy->ID, $key, true ) ) );
		}
	}
	delete_post_meta( $live->ID, '_elementor_element_cache' );
	\Elementor\Core\Files\CSS\Post::create( $live->ID )->delete();
	update_post_meta( $copy->ID, '_wpbruecke_applied', time() );
	$trashed = wp_trash_post( $copy->ID );
	sort( $stale );
	return array(
		'applied'           => true,
		'live'              => $live->ID,
		'copy'              => $copy->ID,
		'copy_trashed'      => (bool) $trashed,
		'live_keys_removed' => $stale,
	);
}

/**
 * Permission: edit_post on the id in the route.
 *
 * @param WP_REST_Request $request Request.
 * @return bool
 */
function wpbruecke_elementor_can_edit( $request ) {
	$id = absint( $request->get_param( 'id' ) );
	return $id > 0 && is_user_logged_in() && current_user_can( 'edit_post', $id );
}

/**
 * Permission for stage: edit_post on the source and the type's edit_posts (D40: a drafter stages only its
 * own drafts).
 *
 * @param WP_REST_Request $request Request.
 * @return bool
 */
function wpbruecke_elementor_can_stage( $request ) {
	if ( ! wpbruecke_elementor_can_edit( $request ) ) {
		return false;
	}
	$post = get_post( absint( $request->get_param( 'id' ) ) );
	$type = $post ? get_post_type_object( $post->post_type ) : null;
	return $type && current_user_can( $type->cap->edit_posts );
}

/**
 * Permission for apply, asked again inside wpbruecke_elementor_apply_copy().
 *
 * @param WP_REST_Request $request Request.
 * @return bool
 */
function wpbruecke_elementor_can_apply_request( $request ) {
	$copy = get_post( absint( $request->get_param( 'id' ) ) );
	if ( ! $copy || ! is_user_logged_in() ) {
		return false;
	}
	$live = get_post( (int) get_post_meta( $copy->ID, '_wpbruecke_source', true ) );
	return $live && wpbruecke_elementor_can_apply( $copy, $live );
}

/**
 * The post named in the route, or a 404.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_Post|WP_Error
 */
function wpbruecke_elementor_route_post( $request ) {
	$post = get_post( absint( $request->get_param( 'id' ) ) );
	return $post ? $post : wpbruecke_elementor_error( 'rest_post_invalid_id', 'Invalid post ID.', 404 );
}

/**
 * A route callback's answer.
 *
 * @param array|WP_Error $result Result.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_elementor_respond( $result ) {
	if ( is_wp_error( $result ) ) {
		return $result;
	}
	$response = rest_ensure_response( $result );
	$response->header( 'Cache-Control', 'no-store' );
	return $response;
}

/**
 * GET elementor/<id>.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_elementor_read( $request ) {
	$post = wpbruecke_elementor_route_post( $request );
	return wpbruecke_elementor_respond( is_wp_error( $post ) ? $post : wpbruecke_elementor_read( $post ) );
}

/**
 * POST elementor/<id>/stage.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_elementor_stage( $request ) {
	$post = wpbruecke_elementor_route_post( $request );
	if ( is_wp_error( $post ) ) {
		return $post;
	}
	try {
		return wpbruecke_elementor_respond( wpbruecke_elementor_stage( $post ) );
	} catch ( \Throwable $e ) {
		return wpbruecke_elementor_error( 'wpbruecke_elementor_error', 'Elementor failed while reading this page (' . get_class( $e ) . '); nothing was written. Look at it in Elementor.', 409 );
	}
}

/**
 * POST elementor/<id>.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_elementor_save( $request ) {
	$post = wpbruecke_elementor_route_post( $request );
	if ( is_wp_error( $post ) ) {
		return $post;
	}
	try {
		return wpbruecke_elementor_respond( wpbruecke_elementor_save( $post, $request->get_param( 'edits' ) ) );
	} catch ( \Throwable $e ) {
		return wpbruecke_elementor_error( 'wpbruecke_elementor_error', 'Elementor failed while reading this page (' . get_class( $e ) . '); nothing was written. Look at it in Elementor.', 409 );
	}
}

/**
 * POST elementor/<copy>/apply.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_elementor_apply( $request ) {
	$post = wpbruecke_elementor_route_post( $request );
	return wpbruecke_elementor_respond( is_wp_error( $post ) ? $post : wpbruecke_elementor_apply_copy( $post ) );
}

/**
 * Register the four routes.
 */
function wpbruecke_register_elementor_routes() {
	$id = array(
		'type'              => 'integer',
		'required'          => true,
		'minimum'           => 1,
		'sanitize_callback' => 'absint',
	);
	register_rest_route(
		'wpbruecke/v1',
		'/elementor/(?P<id>\d+)',
		array(
			array(
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => 'wpbruecke_rest_elementor_read',
				'permission_callback' => 'wpbruecke_elementor_can_edit',
				'args'                => array( 'id' => $id ),
			),
			array(
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => 'wpbruecke_rest_elementor_save',
				'permission_callback' => 'wpbruecke_elementor_can_edit',
				'args'                => array(
					'id'    => $id,
					'edits' => array(
						'type'     => 'array',
						'required' => true,
						'items'    => array( 'type' => 'object' ),
					),
				),
			),
		)
	);
	register_rest_route(
		'wpbruecke/v1',
		'/elementor/(?P<id>\d+)/stage',
		array(
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'wpbruecke_rest_elementor_stage',
			'permission_callback' => 'wpbruecke_elementor_can_stage',
			'args'                => array( 'id' => $id ),
		)
	);
	register_rest_route(
		'wpbruecke/v1',
		'/elementor/(?P<id>\d+)/apply',
		array(
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'wpbruecke_rest_elementor_apply',
			'permission_callback' => 'wpbruecke_elementor_can_apply_request',
			'args'                => array( 'id' => $id ),
		)
	);
}
add_action( 'rest_api_init', 'wpbruecke_register_elementor_routes' );
