/* ============================================================ public API */
/** A full algorithm config from a partial one, e.g. {preset: "popular"} or {weights: {interest: 4}}. */
function makeConfig(partial = {}) {
  const { preset, ...rest } = partial || {};
  const c = defaultConfig();
  if (preset) {
    if (!PRESETS[preset]) throw new Error(`Unknown preset "${preset}". Use one of: ${Object.keys(PRESETS).join(", ")}`);
    applyPreset(c, preset);
  }
  if (rest.weights) rest.weights = { ...c.weights, ...rest.weights };
  return Object.assign(c, rest);
}

/** Rank videos (links, ids or video objects) with an algorithm; returns [{video, score, features, explored}]. */
function rank(videos, config = {}, recentAuthors = []) {
  const list = (videos || []).map((v) => (v && typeof v === "object" && v.url && v.id ? v : toVideo(v))).filter(Boolean);
  return rankAll(list, makeConfig(config), recentAuthors);
}

const MiniTok = {
  version: VERSION,
  mount,
  rank,
  makeConfig,
  presets: PRESETS,
  features: FEATURES,
  starterVideos: STARTER_VIDEOS.map((v) => ({ ...v })),
  Formula,
  FormulaError,
  learn,
  engagement,
  updateInterests,
  parseExport,
  toVideo,
  tokens,
  exportComments,
  commentsFrom,
};
