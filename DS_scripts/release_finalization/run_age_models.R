#!/usr/bin/env Rscript
# Backfill sisal_chronology via SISAL.AM (Carla Roesch, Heidelberg) -- third
# piece of the release finalization pipeline, alongside get_wokam.py /
# get_copernicus_lcc.py (see the "Release finalization pipeline" note in the
# SISAL-Neotoma Obsidian vault).
#
# SISAL.AM runs 5 age-depth model methods (LinReg, LinInterp, Bacon, Bchron,
# StalAge) per entity. The package itself
# (~/.../SQL_and_AgeModel/SISAL.AM, external to this repo, not committed
# here) expects data in the OLD SISALv1b/v2 flat schema -- this script is
# the "loader shim" the vault plan called for: it reads this repo's actual
# csv/*.csv (v3.1, CSV-first) and reshapes it to match what SISAL.AM's R
# functions expect, without touching or requiring the package's own bundled
# .RData (which is SISALv1b/v2 data, not ours).
#
# We source SISAL.AM's individual R/*.R files directly (not library(SISAL.AM)
# / a formal package install) -- confirmed 2026-09-24 that the two Imports
# never actually used at the function level (clam, rlist) don't block this;
# rbacon and Bchron (the two methods that need them) do need to be installed
# and are (see below).
#
# Known schema drift from the old format SISAL.AM's code hardcodes, handled
# below by adding placeholder columns rather than editing the package's own
# R files: `X14C_correction` (ours: `14C_correction`), `COPRA_age`/`linear_age`
# (ours: `copRa_age`, split `lin_interp_age`/`lin_reg_age`), `contact` (ours:
# entity_link_person, not a flat column). All three are confirmed used only
# in throwaway type-casts / a metadata CSV column, never in actual age-model
# math -- see this session's investigation, recorded in CHANGELOG.md.
#
# Bchron calibration curves: SISAL.AM's write_files() maps calib_used to
# Bchron's calCurves as NULL->'normal', 'unknown'->'unknown', 'not
# calibrated'->'ask again', 'INTCAL13 NH'->'intcal13', anything else -> NA.
# In the v3.1 CSVs, U-Th dates have a BLANK calib_used (13,444 of 13,714
# rows); 'not calibrated' (75 rows) and 'unknown' (80) occur ONLY on C14
# dates (corrected 2026-09-25 -- an earlier version of this header claimed
# 'not calibrated' was the majority of U-Th dates, which is wrong). So:
#   * non-C14 dates (U-Th, events): blank/NA/'unknown'/'ask again' -> 'normal'
#     (ages already in calendar years; correct, no calibration needed).
#   * C14 dates: NOT auto-mapped. How SISAL's C14 corr_age relates to
#     calibration (raw 14C age vs already calibrated) has not been verified
#     for this pipeline, so C14-dated entities (SISAL.AM "class III") are
#     skipped by --commit and must be handled by a human (see
#     classify_entities() below).
#
# date_used vs date_used_<method> (decision framed for Laura, 2026-09-25):
# dating.csv has per-method date_used_lin_interp / _lin_reg / _Bchron /
# _Bacon / _StalAge columns besides the generic date_used. In SISALv3 they
# record which dates each published SISAL chronology actually used (an
# OUTPUT of the SISALv2/v3 modelling, blank for entities never modelled --
# e.g. all 25 Glas dates). SISAL.AM itself filters every method on the
# generic date_used == 'yes' only, and so does this script. Recommended: keep
# date_used as the input filter, and (future work) write the per-method
# columns back as provenance for newly modelled entities. Full reasoning:
# agent_susies_scripts/docs/workflows/age-models.md.
#
# Dependencies: R >= 4.2 with dplyr, tidyr, tibble, readr, plyr, Hmisc
# (approxExtrap), rbacon, Bchron (CRAN). JAGS is NOT required. clam/rlist
# (in SISAL.AM's Imports) are not used by the sourced functions. Also needs
# SISAL_AM_PATH pointing at a local checkout of the SISAL.AM package
# (https://github.com/paleovar/SISAL.AM) -- it's not part of this repo.
#
# Usage (run from the repo root):
#   Rscript DS_scripts/release_finalization/run_age_models.R --dry-run [--entities=...]
#   Rscript DS_scripts/release_finalization/run_age_models.R --validate --entities=903,34
#   Rscript DS_scripts/release_finalization/run_age_models.R --commit [--entities=903,720]
#
#   --dry-run   (default) List eligible entities with their SISAL.AM class
#               and whether --commit would run them. Runs nothing.
#   --validate  Run all 5 methods for --entities (required), REGARDLESS of
#               eligibility (so entities with a published sisal_chronology
#               can be re-run and compared), and write nothing to csv/.
#   --commit    Run the age models for eligible class I/II entities (slow --
#               Bacon/Bchron are MCMC) and insert new sisal_chronology rows.
#   --entities  Comma-separated entity_id allowlist.
#
#   --max-resid-ratio=5  Date-fit gate: a method whose median |model - date|
#               exceeds this many median 2-sigma date uncertainties is blanked
#               (not written) and reported as rejected. See date_fit_ratio().
#
# Env vars: SISAL_AM_PATH (package checkout), SISAL_AM_RUNS_DIR (per-entity
# working dirs; default DS_scripts/release_finalization/age_model_runs/,
# git-ignored). Every run writes, per entity, <runs>/<id-name>/
# sisal_chronology_new.csv (all samples, all methods, raw),
# sisal_chronology_gated.csv (same, with gate-rejected methods blanked --
# what --commit writes for missing samples) and a run summary
# <runs>/run_summary_<mode>_<timestamp>.csv (per-method ok / seconds /
# fit ratio, rejected methods, errors).
#
# Eligibility: entity_status == 'current' and >= 1 sample WITHOUT a hiatus
# or gap flag missing a sisal_chronology row (hiatus/gap samples never get a
# row in SISALv3 -- counting them made 86 hiatus entities look eligible
# forever). --commit additionally requires SISAL.AM class I or II (>= 3 used
# dates, U-series only, no non-tractable reversal, depths present), using
# SISAL.AM's own filter_SISAL(). For an eligible entity ALL samples are
# modelled (an age-depth model is computed per entity), but only sample_ids
# with NO sisal_chronology row get a new row; existing rows are never
# touched. Rows where every method is NA (hiatus depths) are not written.
#
# Writing: new rows are merge-inserted into csv/sisal_chronology.csv by
# sample_id as raw text lines (CRLF, 15 significant digits), leaving every
# existing line byte-identical. (The first version re-wrote the whole file
# through readr, which changes CRLF->LF and the float formatting of ~182k
# rows -- a 320k-line diff for a 500-row change.)
#
# After running, rebuild and verify before committing anyway:
#   python3 USER_scripts/build_db.py /tmp/sisal_check

suppressMessages({
  library(dplyr)
  library(tidyr)
  library(tibble)
  library(readr)
  library(rbacon)
  library(Bchron)
})

REPO_ROOT <- normalizePath(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(trailingOnly = FALSE), value = TRUE))), "..", ".."))
CSV_DIR <- file.path(REPO_ROOT, "csv")
RUNS_DIR <- normalizePath(Sys.getenv("SISAL_AM_RUNS_DIR", file.path(REPO_ROOT, "DS_scripts", "release_finalization", "age_model_runs")), mustWork = FALSE)

# Local checkout of the SISAL.AM package -- external to this repo (Carla
# Roesch's own R package, not ours to redistribute). Override with the
# SISAL_AM_PATH env var if it lives somewhere else.
SISAL_AM_PATH <- Sys.getenv(
  "SISAL_AM_PATH",
  "/Users/lendres/Documents/ResearchHome/00_Researchtopics/BB_Working Groups/AB_SISAL/SISAL-Neo cont./SQL_and_AgeModel/SISAL.AM"
)
if (!dir.exists(SISAL_AM_PATH)) {
  stop("SISAL.AM package not found at: ", SISAL_AM_PATH, "\nSet SISAL_AM_PATH to the correct local checkout.")
}
source(file.path(SISAL_AM_PATH, "R", "functions.R"))
source(file.path(SISAL_AM_PATH, "R", "age_model.R"))
source(file.path(SISAL_AM_PATH, "R", "StalAge_1_0_modified.R"))

# ---------------------------------------------------------------- lin_reg_ages() if() vector-condition shim
# lin_reg_ages()'s hiatus branch (functions.R, only reached for entities
# with a hiatus) does `if (!is.na(m[[i]])) {...}`, where m[[i]] is either
# the scalar NA (no fitted section) or a real `lm` object -- and `lm`
# objects are lists (~12 named components: coefficients, residuals,
# fitted.values, ...), so is.na() on a real one returns a multi-element
# vector, not a scalar. Same R 4.2+ "condition has length > 1" break as
# the Bacon fix above (confirmed 2026-09-24, entity 583/BG41). Fixed with
# the same approach: `[1]` replicates the documented pre-4.2 R behaviour
# (first element used, with a warning) exactly -- correct here too, since
# is.na() on a real lm object is all-FALSE (first element FALSE -> !FALSE
# = TRUE, correctly entering the branch), and is.na(NA) is a scalar TRUE
# either way (first element unambiguous). A full copy of lin_reg_ages()
# rather than editing functions.R in place.
#
# Second fix in the same copy (2026-09-25, the "$ operator is invalid for
# atomic vectors" error left open on 2026-09-24 for entity 583/BG41): for
# the section BELOW the last hiatus, upstream tests is.na(m[[1]]) (the FIRST
# section) but then dereferences m[[length(hiatus_tb) + 1]] (the LAST).
# linear_regression() sets a section to NA when it holds < 2 dates -- BG41
# has one date below its hiatus at 61 mm -- so the test passed on m[[1]] and
# `NA$coefficients` failed. Now tests the section it actually uses, so a
# section with < 2 dates gets NA ages (no regression possible), which is
# what the NA branch was clearly written for.
lin_reg_ages <- function(m, depth_eval, hiatus_tb) {
  d <- length(unlist(depth_eval))
  j <- length(m)
  lin_reg_age <- replicate(d, 0)

  for (i in seq(1, j)) {
    if (!is.na(m[[i]])[1]) {
      for (k in c(1, 2)) {
        if (is.na(m[[i]]$coefficients[[k]])) {
          m[[i]]$coefficients[[k]] <- 0
        }
      }
    }
  }

  depth <- cbind(depth_eval, lin_reg_age)

  idx <- depth[, 1] < hiatus_tb[1]
  if (is.na(m[[1]])[1]) {
    depth[idx, 2] <- NA
  } else {
    depth[idx, 2] <- m[[1]]$coefficients[[1]] + depth[idx, 1] * m[[1]]$coefficients[[2]]
  }

  if (j > 2) {
    for (i in seq(2, length(hiatus_tb))) {
      idx <- (depth[, 1] < hiatus_tb[i]) & (depth[, 1] > hiatus_tb[i - 1])
      if (is.na(m[[i]])[1]) {
        depth[idx, 2] <- NA
      } else {
        depth[idx, 2] <- m[[i]]$coefficients[[1]] + depth[idx, 1] * m[[i]]$coefficients[[2]]
      }
    }
    idx <- depth[, 1] > hiatus_tb[length(hiatus_tb)]
    if (is.na(m[[length(hiatus_tb) + 1]])[1]) {
      depth[idx, 2] <- NA
    } else {
      depth[idx, 2] <- m[[length(hiatus_tb) + 1]]$coefficients[[1]] + depth[idx, 1] * m[[length(hiatus_tb) + 1]]$coefficients[[2]]
    }
  } else {
    idx <- depth[, 1] > hiatus_tb[length(hiatus_tb)]
    if (is.na(m[[length(hiatus_tb) + 1]])[1]) {
      depth[idx, 2] <- NA
    } else {
      depth[idx, 2] <- m[[length(hiatus_tb) + 1]]$coefficients[[1]] + depth[idx, 1] * m[[length(hiatus_tb) + 1]]$coefficients[[2]]
    }
  }

  data <- data.frame(depth_eval = depth[, 1], lin_reg_age = depth[, 2])
  h <- data.frame(depth_eval = hiatus_tb, lin_reg_age = replicate(length(hiatus_tb), NA))
  data <- rbind(data, h)
  data <- data[order(data[, 1]), ]
  data
}

# ---------------------------------------------------------------- mc_linReg() typo fix
# mc_linReg()'s hiatus branch (functions.R, only reached for entities that
# HAVE a hiatus) calls `linear_regression_ages(...)`, a function that exists
# nowhere in the package -- confirmed 2026-09-24 (entity 583/BG41, which has
# a hiatus, failed with "could not find function"; entity 903/Glas, no
# hiatus, never hit this branch and succeeded). The only function with a
# matching signature and purpose is `lin_reg_ages(m, depth_eval, hiatus_tb)`
# (the fixed copy above), called positionally with identical arguments --
# an unmistakable rename-that-missed-two-call-sites bug. Fixed via alias
# rather than editing functions.R in place.
linear_regression_ages <- lin_reg_ages

# ---------------------------------------------------------------- Bchron API-drift shim
# SISAL.AM's runBchron() (2019-2020) calls Bchronology(..., jitterPositions = T),
# an argument that no longer exists in the currently installed Bchron (4.7.8) --
# confirmed 2026-09-24 via args(Bchronology). Rather than editing SISAL.AM's own
# R file, this is a copy of runBchron with only that one argument removed;
# everything else (file I/O, hiatus handling, output columns/filenames) is
# identical to the original, so merge/read-back logic downstream is unaffected.
runBchron_shim <- function(working_directory, file_name) {
  setwd(file.path(working_directory, file_name, "/Bchron"))
  dating_tb <- read.csv("ages.csv", header = TRUE, stringsAsFactors = FALSE)
  depth_sample <- read.csv("depths.csv", header = TRUE, stringsAsFactors = FALSE, colClasses = c("numeric", "numeric"))
  depth_eval <- depth_sample$depth_sample

  setwd(file.path(working_directory, file_name))
  hiatus_tb <- read.csv("hiatus.csv", header = TRUE, stringsAsFactors = FALSE, colClasses = c("numeric", "numeric"))

  if (!plyr::empty(data.frame(hiatus_tb))) {
    dating_tb <- add_hiatus(dating_tb, hiatus_tb, bchron = TRUE)
    write.csv(dating_tb, "hiatus_dates_bchron.csv", row.names = FALSE)
  }

  run <- Bchronology(
    ages = dating_tb$corr_age, ageSds = dating_tb$corr_age_uncert,
    positions = dating_tb$depth_dating_new, positionThicknesses = dating_tb$thickness_new,
    calCurves = dating_tb$calib_curve_new, ids = dating_tb$dating_id,
    predictPositions = depth_eval
  )

  mcmc <- run$thetaPredict
  bchron_age <- apply(mcmc, 2, median)
  bchron_quantile <- apply(mcmc, 2, function(x) quantile(x, probs = c(0.05, 0.95), na.rm = TRUE))

  d <- data.frame(cbind(
    depth_eval, bchron_age,
    bchron_age_uncert_pos = bchron_quantile[2, ] - bchron_age,
    bchron_age_uncert_neg = bchron_age - bchron_quantile[1, ]
  ))

  if (!plyr::empty(data.frame(hiatus_tb))) {
    hiatus_new <- hiatus_tb %>% mutate(depth_sample = depth_sample / 10)
    d <- d %>% mutate(
      bchron_age = if_else(depth_eval %in% hiatus_new$depth_sample, NA_real_, bchron_age),
      bchron_age_uncert_pos = if_else(depth_eval %in% hiatus_new$depth_sample, NA_real_, bchron_age_uncert_pos),
      bchron_age_uncert_neg = if_else(depth_eval %in% hiatus_new$depth_sample, NA_real_, bchron_age_uncert_neg)
    )
  }

  setwd(file.path(working_directory, file_name, "/Bchron"))
  write.table(mcmc, "bchron_ensemble.txt", col.names = FALSE, row.names = FALSE)
  b_chrono <- data.frame(
    sample_id = depth_sample$sample_id, bchron_age = d[, 2],
    bchron_age_uncert_pos = d[, 3], bchron_age_uncert_neg = d[, 4]
  )
  write.csv(b_chrono, "bchron_chronology.csv", row.names = FALSE)
}

# ---------------------------------------------------------------- Bacon if() vector-condition shim
# SISAL.AM's runBacon() computes `k <- seq(floor(min(depth_eval)), ceiling(max(depth_eval)), by = 5)`
# -- a multi-element vector whenever an entity's depth range exceeds 5 (cm,
# post /10 conversion) -- then does `if (k < 10) {...} else if (k > 20) {...}`.
# R < 4.2 silently used only the first element for a vector `if` condition
# (with a warning); R 4.2+ makes this a hard error ("the condition has
# length > 1"). Confirmed 2026-09-24: entity 903/Glas (narrow depth range,
# k length 1) succeeded; entity 583/BG41 (wider range, k length > 1) failed
# with exactly this error. Fixed by using k[1] for both condition checks --
# replicating the documented pre-4.2 R behaviour exactly (not a reinterpretation
# of intent), so output for any entity that worked under the old R is unchanged.
# Two more guards added 2026-09-25 (see inline comments): an explicit error
# when upstream leaves `thickness` undefined, and a Bacon() failure now
# propagates (upstream only wrote bacon_error.txt and carried on, so
# Bacon.Age.d() could read a stale model from the previous entity).
runBacon_shim <- function(working_directory, file_name, postbomb = 0, cc = 0) {
  setwd(file.path(working_directory, file_name, "/Bacon_runs/", file_name))
  depth_eval <- matrix(read.table(paste0(file_name, "_depths.txt"), col.names = ""))[[1]]
  sample_id <- read.csv("sample_id.csv", header = TRUE, stringsAsFactors = FALSE, colClasses = c("numeric"))
  hiatus_tb <- read.csv("hiatus_bacon.csv", header = TRUE, stringsAsFactors = FALSE, colClasses = c("numeric", "numeric"))
  core <- read.csv(paste0(file_name, ".csv"), header = TRUE, stringsAsFactors = FALSE, colClasses = c("numeric", "numeric", "numeric", "numeric"))

  accMean <- sapply(c(1, 2, 5), function(x) x * 10^(-1:2))
  ballpacc <- lm(core[, 2] * 1.1 ~ core[, 4])$coefficients[2]
  ballpacc <- abs(accMean - ballpacc)
  ballpacc <- ballpacc[ballpacc > 0]
  accMean <- accMean[order(ballpacc)[1]]

  k <- seq(floor(min(depth_eval, na.rm = TRUE)), ceiling(max(depth_eval, na.rm = TRUE)), by = 5)
  thickness <- NULL
  if (k[1] < 10) {
    thickness <- pretty(5 * (k / 10), 10)
    thickness <- min(thickness[thickness > 0])
  } else if (k[1] > 20) {
    thickness <- max(pretty(5 * (k / 20)))
  }
  # Upstream leaves `thickness` undefined when 10 <= k[1] <= 20 (top sample
  # 10-20 cm below the top) -- fail explicitly instead of an obscure error.
  if (is.null(thickness)) stop("Bacon section thickness undefined for top depth ", k[1], " cm (upstream gap for 10-20 cm); needs a steward decision")

  # Bacon.Age.d() reads rbacon's global `info` object. Clear it so a failed
  # Bacon() run can never silently reuse the PREVIOUS entity's model.
  if (exists("info", envir = globalenv())) rm("info", envir = globalenv())

  j <- 2000
  tho <- c()

  setwd(file.path(working_directory, file_name))
  if (dim(hiatus_tb)[1] == 0) {
    tryCatch(
      { Bacon(core = file_name, depths.file = TRUE, thick = thickness, acc.mean = accMean, postbomb = postbomb, cc = cc, suggest = FALSE, ask = FALSE, ssize = j, th0 = tho) },
      error = function(e) { write.table(x = paste("ERROR in Bacon:", conditionMessage(e)), file = "bacon_error.txt"); stop("Bacon failed: ", conditionMessage(e)) }
    )
  } else {
    tryCatch(
      { Bacon(core = file_name, depths.file = TRUE, thick = thickness, acc.mean = accMean, postbomb = postbomb, hiatus.depths = hiatus_tb$depth_sample_bacon, cc = cc, suggest = FALSE, ask = FALSE, ssize = j, th0 = tho) },
      error = function(e) { write.table(x = paste("ERROR in Bacon:", conditionMessage(e)), file = "bacon_error.txt"); stop("Bacon failed: ", conditionMessage(e)) }
    )
  }

  bacon_mcmc <- sapply(depth_eval, Bacon.Age.d)
  bacon_age <- get_bacon_median_quantile(depth_eval, hiatus_tb, bacon_mcmc)
  bacon_mcmc <- rbind(depth_eval, bacon_mcmc)
  bacon_mcmc <- t(bacon_mcmc)
  bacon_mcmc <- cbind(sample_id, bacon_mcmc)

  h <- cbind(hiatus_tb, matrix(NA, nrow = dim(hiatus_tb)[1], ncol = dim(bacon_mcmc)[2] - 2))
  names(h) <- names(bacon_mcmc)

  bacon_mcmc <- rbind(bacon_mcmc, h)
  bacon_mcmc <- bacon_mcmc[order(bacon_mcmc[, 2]), ]
  sample_id <- bacon_mcmc[, 1]

  setwd(file.path(working_directory, file_name, "/Bacon_runs"))
  write.table(bacon_mcmc, "mc_bacon_ensemble.txt", col.names = FALSE, row.names = FALSE)
  write.csv(cbind(sample_id, bacon_age[, 2:4]), "bacon_chronology.csv", row.names = FALSE)
}

dir.create(RUNS_DIR, showWarnings = FALSE, recursive = TRUE)

# ---------------------------------------------------------------- CLI args
args <- commandArgs(trailingOnly = TRUE)
mode <- if ("--commit" %in% args) "commit" else if ("--validate" %in% args) "validate" else "dry-run"
if (all(c("--commit", "--validate") %in% args)) stop("--commit and --validate are mutually exclusive")
entities_arg <- grep("^--entities=", args, value = TRUE)
entity_allowlist <- if (length(entities_arg) > 0) {
  as.integer(strsplit(sub("^--entities=", "", entities_arg[1]), ",")[[1]])
} else {
  NULL
}
if (mode == "validate" && is.null(entity_allowlist)) stop("--validate needs --entities=id1,id2,...")
ratio_arg <- grep("^--max-resid-ratio=", args, value = TRUE)
MAX_RESID_RATIO <- if (length(ratio_arg) > 0) as.numeric(sub("^--max-resid-ratio=", "", ratio_arg[1])) else 5

# ---------------------------------------------------------------- load CSVs
read_repo_csv <- function(name) read_csv(file.path(CSV_DIR, paste0(name, ".csv")), show_col_types = FALSE)

dating_raw <- read_repo_csv("dating")
sample_raw <- read_repo_csv("sample")
hiatus_raw <- read_repo_csv("hiatus")
gap_raw <- read_repo_csv("gap")
entity_raw <- read_repo_csv("entity")
site_raw <- read_repo_csv("site")
notes_raw <- read_repo_csv("notes")
entity_link_reference_raw <- read_repo_csv("entity_link_reference")
reference_raw <- read_repo_csv("reference")
original_chronology_raw <- read_repo_csv("original_chronology")
sisal_chronology_raw <- read_repo_csv("sisal_chronology")
d13C_raw <- read_repo_csv("d13C")
d18O_raw <- read_repo_csv("d18O")
dating_lamina_raw <- read_repo_csv("dating_lamina")

# ---------------------------------------------------------------- schema-drift shims
# `X14C_correction`: placeholder only (see file header) -- carry the real
# values under the name the code expects.
dating_tb_in <- dating_raw %>% mutate(X14C_correction = `14C_correction`)

# `COPRA_age*`: alias of our `copRa_age*`. `linear_age*`: no v3.1 equivalent
# (split into lin_interp_age/lin_reg_age) -- NA placeholder, confirmed
# unused beyond a type-cast (see file header).
sisal_chronology_tb_in <- sisal_chronology_raw %>%
  mutate(
    COPRA_age = copRa_age, COPRA_age_uncert_pos = copRa_age_uncert_pos, COPRA_age_uncert_neg = copRa_age_uncert_neg,
    linear_age = NA_real_, linear_age_uncert_pos = NA_real_, linear_age_uncert_neg = NA_real_
  )

# `contact`: no v3.1 equivalent (replaced by entity_link_person) -- cosmetic
# only, written into a metadata CSV nothing downstream reads.
entity_tb_in <- entity_raw %>% mutate(contact = NA_character_)

# write_files() references `dating_lamina` as a bare global (not a
# parameter) -- assign it at top level so R's lexical scoping finds it.
dating_lamina <- dating_lamina_raw

# ---------------------------------------------------------------- entity selection
# Hiatus/gap samples never get a sisal_chronology row, so they don't count
# as "missing" (otherwise every hiatus entity stays eligible forever).
hiatus_gap_samples <- union(hiatus_raw$sample_id, gap_raw$sample_id)
missing_chrono_samples <- setdiff(setdiff(sample_raw$sample_id, sisal_chronology_raw$sample_id), hiatus_gap_samples)

# SISAL.AM's own classification (filter_SISAL, functions.R): I = U-series,
# no hiatus; II = U-series with hiatus; III = no U-series dates; IV = < 3
# used dates or non-tractable reversals. Upstream code, called unmodified.
classify_entities <- function() {
  cls <- lapply(1:4, function(m) {
    ids <- filter_SISAL(m, dating. = dating_tb_in, sample. = sample_raw, entity. = entity_raw, hiatus. = hiatus_raw)$entity_id
    tibble(entity_id = as.numeric(ids), class = c("I", "II", "III", "IV")[m])
  })
  bind_rows(cls) %>% group_by(entity_id) %>% summarise(class = paste(class, collapse = "+"), .groups = "drop")
}

used_dates <- dating_raw %>% filter(date_used == "yes", date_type != "Event; hiatus")
entity_info <- entity_raw %>%
  filter(entity_status == "current") %>%
  select(entity_id, entity_name) %>%
  left_join(sample_raw %>% group_by(entity_id) %>% summarise(n_samples = n(), n_missing = sum(sample_id %in% missing_chrono_samples), .groups = "drop"), by = "entity_id") %>%
  left_join(used_dates %>% group_by(entity_id) %>% summarise(n_used_dates = n(), has_C14 = any(date_type == "C14"), .groups = "drop"), by = "entity_id") %>%
  left_join(sample_raw %>% filter(sample_id %in% hiatus_raw$sample_id) %>% count(entity_id, name = "n_hiatus"), by = "entity_id") %>%
  left_join(classify_entities(), by = "entity_id") %>%
  mutate(across(c(n_samples, n_missing, n_used_dates, n_hiatus), ~ coalesce(as.integer(.x), 0L)),
         has_C14 = coalesce(has_C14, FALSE), class = coalesce(class, "-"),
         commit_action = case_when(
           n_missing == 0 ~ "nothing missing",
           has_C14 ~ "skip: C14 dates (calibration handling unverified)",
           class == "-" ~ "skip: no SISAL.AM class (no sample depths or no dates)",
           !grepl("^(I|II)$", class) ~ paste0("skip: SISAL.AM class ", class),
           TRUE ~ "run"))

eligible_entities <- entity_info %>% filter(n_missing > 0)
if (!is.null(entity_allowlist)) {
  missing_ids <- setdiff(entity_allowlist, entity_info$entity_id)
  if (length(missing_ids) > 0) cat("Not a current entity (ignored):", missing_ids, "\n")
  eligible_entities <- eligible_entities %>% filter(entity_id %in% entity_allowlist)
}

cat(sprintf("\n=== %d entities eligible (current, >=1 non-hiatus/gap sample missing sisal_chronology) ===\n", nrow(eligible_entities)))
print(eligible_entities %>% count(commit_action), n = 50)

if (mode == "dry-run") {
  print(eligible_entities %>% select(-n_samples), n = min(60, nrow(eligible_entities)), width = 200)
  cat("\nDry run -- no age models run. --validate --entities=... runs without writing; --commit runs the 'run' rows and writes.\n")
  quit(status = 0)
}

to_run <- if (mode == "validate") {
  entity_info %>% filter(entity_id %in% entity_allowlist)
} else {
  eligible_entities %>% filter(commit_action == "run")
}
if (nrow(to_run) == 0) {
  cat("Nothing to run.\n")
  quit(status = 0)
}

# ---------------------------------------------------------------- per-entity run
# Bchron calCurves (see header): non-C14 dates -> 'normal'; C14 dates keep
# their mapped curve and fail loudly if SISAL.AM left no real curve name.
fix_bchron_calib <- function(bchron_ages_path) {
  ages <- read_csv(bchron_ages_path, show_col_types = FALSE)
  if (!"calib_curve_new" %in% names(ages)) return(invisible(NULL))
  types <- dating_raw$date_type[match(ages$dating_id, dating_raw$dating_id)]
  is_c14 <- !is.na(types) & types == "C14"
  bad <- is.na(ages$calib_curve_new) | ages$calib_curve_new %in% c("ask again", "unknown")
  if (any(is_c14 & bad)) stop("C14 date(s) without a usable calibration curve: dating_id ", paste(ages$dating_id[is_c14 & bad], collapse = ","))
  ages$calib_curve_new[!is_c14 & bad] <- "normal"
  write_csv(ages, bchron_ages_path)
}

run_one_entity <- function(entid, entity_name) {
  file_name <- paste0(entid, "-", entity_name)
  result <- list(entity_id = entid, file_name = file_name, ok = list(), secs = list(), error = list())
  # Start from an empty working dir: write_files() reuses an existing one, and
  # a stale *_chronology.csv from an earlier run must never be read back as
  # this run's result.
  unlink(file.path(RUNS_DIR, file_name), recursive = TRUE)

  wf_ok <- tryCatch({
    write_files(entid, bacon = TRUE, bchron = TRUE, stalage = TRUE, linInterp = TRUE, linReg = TRUE,
                dating. = dating_tb_in, working_directory = RUNS_DIR, site. = site_raw, entity. = entity_tb_in,
                entity_link_reference. = entity_link_reference_raw, reference. = reference_raw, notes. = notes_raw,
                sample. = sample_raw, hiatus. = hiatus_raw, gap. = gap_raw,
                original_chronology. = original_chronology_raw, sisal_chronology. = sisal_chronology_tb_in,
                d13C. = d13C_raw, d18O. = d18O_raw)
    TRUE
  }, error = function(e) {
    result$error[["write_files"]] <<- conditionMessage(e)
    FALSE
  })
  if (!wf_ok) return(result)

  for (method in c("linReg", "linInterp", "Bacon", "Bchron", "StalAge")) {
    cat(sprintf("--- entity %s: %s ...\n", entid, method))
    t0 <- Sys.time()
    ok <- tryCatch({
      switch(method,
        linReg = runLinReg(RUNS_DIR, file_name),
        linInterp = runLinInterp(RUNS_DIR, file_name),
        Bacon = runBacon_shim(RUNS_DIR, file_name),
        Bchron = { fix_bchron_calib(file.path(RUNS_DIR, file_name, "Bchron", "ages.csv")); runBchron_shim(RUNS_DIR, file_name) },
        StalAge = runStalAge(RUNS_DIR, file_name)
      )
      TRUE
    }, error = function(e) {
      result$error[[method]] <<- conditionMessage(e)
      FALSE
    })
    grDevices::graphics.off()
    result$ok[[method]] <- ok
    result$secs[[method]] <- round(as.numeric(difftime(Sys.time(), t0, units = "secs")), 1)
    cat(sprintf("--- entity %s: %s %s (%.1f s)\n", entid, method, if (ok) "OK" else "FAILED", result$secs[[method]]))
  }
  setwd(REPO_ROOT)
  result
}

cat(sprintf("\nRunning age models for %d entities (%s mode; Bacon/Bchron are MCMC, expect ~1-3 min per entity)...\n", nrow(to_run), mode))
run_results <- Map(run_one_entity, to_run$entity_id, to_run$entity_name)

# ---------------------------------------------------------------- collect + merge results
method_files <- list(
  linReg = c("linReg", "linReg_chronology.csv", "lin_reg_age", "lin_reg_age"),
  linInterp = c("linInterp", "linInt_chronology.csv", "lin_interp_age", "lin_interp_age"),
  Bacon = c("Bacon_runs", "bacon_chronology.csv", "bacon_age", "Bacon_age"),
  Bchron = c("Bchron", "bchron_chronology.csv", "bchron_age", "Bchron_age"),
  StalAge = c("StalAge", "StalAge_chronology.csv", "StalAge_age", "StalAge_age")
)
read_method_chrono <- function(file_name, spec) {
  path <- file.path(RUNS_DIR, file_name, spec[1], spec[2])
  if (!file.exists(path)) return(NULL)
  age_col <- spec[3]; rename_to <- spec[4]
  df <- suppressMessages(read_csv(path, show_col_types = FALSE))
  df <- df %>% select(sample_id, all_of(c(age_col, paste0(age_col, "_uncert_pos"), paste0(age_col, "_uncert_neg"))))
  names(df) <- c("sample_id", rename_to, paste0(rename_to, "_uncert_pos"), paste0(rename_to, "_uncert_neg"))
  df %>% filter(!is.na(sample_id)) %>% distinct(sample_id, .keep_all = TRUE)
}

schema_cols <- names(sisal_chronology_raw)
age_cols <- setdiff(schema_cols, "sample_id")

# Date-fit gate (added 2026-09-25 after Bacon silently mis-fitted Glas/903 by
# ~4,000 yr while reporting success): for each method, the median |model -
# date| at the used dates' depths, divided by the median 2-sigma date
# uncertainty. Above MAX_RESID_RATIO (default 5, --max-resid-ratio=) the
# method's columns are blanked before anything is written, and the method is
# reported as "rejected". Deliberately loose: it catches gross failures
# (Glas Bacon ~58), not a regression line that misses curvature (Glas
# lin_reg ~2). Depths come from the run dir, already converted from-top.
method_col <- c(linReg = "lin_reg_age", linInterp = "lin_interp_age", Bacon = "Bacon_age", Bchron = "Bchron_age", StalAge = "StalAge_age")
date_fit_ratio <- function(merged, file_name, col) {
  dates <- suppressMessages(read_csv(file.path(RUNS_DIR, file_name, "used_dates.csv"), show_col_types = FALSE))
  depths <- suppressMessages(read_csv(file.path(RUNS_DIR, file_name, "proxy_data.csv"), show_col_types = FALSE)) %>% select(sample_id, depth_sample)
  d <- merged %>% select(sample_id, age = all_of(col)) %>% inner_join(depths, by = "sample_id") %>% filter(!is.na(age), !is.na(depth_sample)) %>% arrange(depth_sample)
  if (nrow(d) < 2 || nrow(dates) == 0) return(NA_real_)
  model <- approx(d$depth_sample, d$age, xout = dates$depth_dating, rule = 1, ties = mean)$y
  ok <- !is.na(model)
  if (!any(ok)) return(NA_real_)
  median(abs(model[ok] - dates$corr_age[ok])) / median(((dates$corr_age_uncert_pos + dates$corr_age_uncert_neg) / 2)[ok])
}
new_rows <- list()
summary_rows <- list()

for (r in run_results) {
  entid <- r$entity_id
  ok_methods <- names(Filter(isTRUE, r$ok))
  fit_ratio <- list(); rejected <- character(0)
  parts <- Filter(Negate(is.null), lapply(method_files[ok_methods], function(spec) read_method_chrono(r$file_name, spec)))
  merged <- NULL
  if (length(parts) > 0) {
    merged <- Reduce(function(a, b) full_join(a, b, by = "sample_id"), parts)
    for (col in setdiff(schema_cols, names(merged))) merged[[col]] <- NA_real_
    merged <- merged %>% select(all_of(schema_cols)) %>% mutate(across(everything(), as.numeric))
    # hiatus depths come back as all-NA rows -- SISALv3 has no row for them
    merged <- merged[rowSums(!is.na(merged[, age_cols])) > 0, ]
    merged <- merged %>% arrange(sample_id)
    write_csv(merged, file.path(RUNS_DIR, r$file_name, "sisal_chronology_new.csv"), na = "")
    for (m in ok_methods) {
      ratio <- date_fit_ratio(merged, r$file_name, method_col[[m]])
      fit_ratio[[m]] <- ratio
      if (!is.na(ratio) && ratio > MAX_RESID_RATIO) {
        rejected <- c(rejected, m)
        merged[, grep(paste0("^", method_col[[m]]), names(merged))] <- NA_real_
      }
    }
    merged <- merged[rowSums(!is.na(merged[, age_cols])) > 0, ]
    # what would actually be written (rejected methods blanked)
    write_csv(merged, file.path(RUNS_DIR, r$file_name, "sisal_chronology_gated.csv"), na = "")
  }
  to_write <- if (is.null(merged)) merged else merged %>% filter(sample_id %in% missing_chrono_samples)
  if (mode == "commit" && !is.null(to_write) && nrow(to_write) > 0) new_rows[[length(new_rows) + 1]] <- to_write
  row <- tibble(entity_id = entid, file_name = r$file_name)
  for (m in names(method_files)) {
    row[[paste0(m, "_ok")]] <- isTRUE(r$ok[[m]])
    row[[paste0(m, "_s")]] <- if (is.null(r$secs[[m]])) NA_real_ else r$secs[[m]]
    row[[paste0(m, "_fit")]] <- if (is.null(fit_ratio[[m]])) NA_real_ else round(fit_ratio[[m]], 2)
  }
  row$rejected <- paste(rejected, collapse = ",")
  row$rows_modelled <- if (is.null(merged)) 0L else nrow(merged)
  row$rows_missing_filled <- if (is.null(to_write)) 0L else nrow(to_write)
  row$errors <- paste(sprintf("%s: %s", names(r$error), vapply(r$error, function(e) strsplit(e, "\n")[[1]][1], "")), collapse = " | ")
  summary_rows[[length(summary_rows) + 1]] <- row
}

summary_tb <- bind_rows(summary_rows)
summary_path <- file.path(RUNS_DIR, sprintf("run_summary_%s_%s.csv", mode, format(Sys.time(), "%Y%m%d-%H%M%S")))
write_csv(summary_tb, summary_path, na = "")
cat("\n=== Per-entity method success (s = seconds) ===\n")
print(summary_tb %>% select(-file_name, -errors), n = nrow(summary_tb), width = 200)
cat("\n=== Errors (first line per failed step) ===\n")
for (i in seq_len(nrow(summary_tb))) if (nzchar(summary_tb$errors[i])) cat(sprintf("  entity_id %s: %s\n", summary_tb$entity_id[i], summary_tb$errors[i]))
cat("Run summary:", summary_path, "\n")

if (mode == "validate") {
  cat("\nValidate mode -- nothing written to csv/. Per-entity results: <runs>/<id-name>/sisal_chronology_new.csv\n")
  quit(status = 0)
}
if (length(new_rows) == 0) {
  cat("\nNo new sisal_chronology rows produced -- nothing to write.\n")
  quit(status = 0)
}

# ---------------------------------------------------------------- write (merge-insert, existing lines untouched)
new_chrono <- bind_rows(new_rows) %>% distinct(sample_id, .keep_all = TRUE) %>% arrange(sample_id)
stopifnot(!any(new_chrono$sample_id %in% sisal_chronology_raw$sample_id))
fmt_num <- function(x) ifelse(is.na(x), "", sprintf("%.15g", x))
new_lines <- do.call(paste, c(list(sprintf("%d", as.integer(new_chrono$sample_id))), lapply(new_chrono[age_cols], fmt_num), sep = ","))

chrono_path <- file.path(CSV_DIR, "sisal_chronology.csv")
old_lines <- readLines(chrono_path, warn = FALSE)  # accepts CRLF, strips it
header <- old_lines[1]
if (header != paste(schema_cols, collapse = ",")) stop("Unexpected sisal_chronology.csv header: ", header)
body <- old_lines[-1]
all_ids <- c(as.integer(sub(",.*$", "", body)), as.integer(new_chrono$sample_id))
all_lines <- c(body, new_lines)
all_lines <- all_lines[order(all_ids, method = "radix")]
con <- file(chrono_path, open = "wb")
writeLines(c(header, all_lines), con, sep = "\r\n")
close(con)

cat(sprintf("\nInserted %d new sisal_chronology row(s) into csv/sisal_chronology.csv (existing rows untouched)\n", nrow(new_chrono)))
cat("Next: git diff --stat csv/  then  python3 USER_scripts/build_db.py /tmp/sisal_check\n")
