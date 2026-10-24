-- Department totals.
--
-- The NBI rate is weighted by households, not averaged across districts. An
-- unweighted mean would give a district of 900 households the same say as one
-- of 90,000, which is the single most common way a regional average misleads.

select
    department_code,
    department_name,
    year,
    count(*)                                        as districts,
    sum(population)                                 as population,
    sum(households)                                 as households,
    sum(households_with_nbi)                        as households_with_nbi,

    100.0 * sum(households_with_nbi) / nullif(sum(households), 0)
                                                    as nbi_any_pct_weighted,
    avg(nbi_any_pct)                                as nbi_any_pct_unweighted,

    -- Published side by side on purpose: the gap between them is how much the
    -- weighting matters in this department.
    100.0 * sum(households_with_nbi) / nullif(sum(households), 0)
        - avg(nbi_any_pct)                          as weighting_effect_points,

    avg(median_age)                                 as median_age_avg,
    count(*) filter (where is_new_since_2012)        as districts_new_since_2012

from {{ ref('fct_district_profile') }}
group by 1, 2, 3
