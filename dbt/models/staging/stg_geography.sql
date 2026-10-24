-- The district dimension, with the reconciliation flags named for a reader
-- rather than for the loader.

select
    geo_key,
    department_code,
    department_name,
    district_name,
    district_name_normalised,

    vintage_2012_key                       as key_as_published_2012,
    name_2012                              as district_name_2012,
    name_changed_since_2012                as was_renamed_since_2012,
    created_after_2012                     as is_new_since_2012,

    -- A district reconciles to the 2012 register only if it existed then.
    (vintage_2012_key is not null)         as reconciles_to_2012,

    -- The published 2012 key is unpadded, so it does not join to the 2022 key
    -- without normalisation. This flags the rows where that bites.
    (
        vintage_2012_key is not null
        and not regexp_matches(vintage_2012_key, '^\d{4}$')
    )                                      as key_needed_padding,

    encoding_damage,
    (encoding_damage <> '')                as has_encoding_damage

from {{ source('warehouse', 'dim_geography') }}
