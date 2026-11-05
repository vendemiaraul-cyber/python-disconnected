import { test } from "node:test";
import assert from "node:assert/strict";
import MiniTok, { rank, makeConfig, Formula, FormulaError, learn, parseExport, exportComments, toVideo } from "../dist/minitok.mjs";

const vids = [
  { id: "1000000001", author: "cats", description: "so cute #cats", views: 1e3, likes: 100, duration: 10 },
  { id: "1000000002", author: "news", description: "breaking #news", views: 5e7, likes: 5e6, duration: 60 },
].map(toVideo);

test("toVideo understands links, ids and objects", () => {
  assert.equal(toVideo("https://www.tiktok.com/@hankgreen1/video/7047596209028074758").author, "hankgreen1");
  assert.equal(toVideo("https://www.tiktok.com/player/v1/6718335390845095173").id, "6718335390845095173");
  assert.equal(toVideo("7047596209028074758").id, "7047596209028074758");
  assert.equal(toVideo({ id: "7047596209028074758", author: "x" }).author, "x");
  assert.equal(toVideo("https://example.com/video/1"), null);
  assert.equal(toVideo(""), null);
});

test("presets change the ranking", () => {
  const niche = rank(vids, { preset: "niche", interests: { "#cats": 2 }, exploration: 0 });
  assert.equal(niche[0].video.author, "cats");
  const popular = rank(vids, { preset: "popular", exploration: 0 });
  assert.equal(popular[0].video.author, "news");
  assert.throws(() => makeConfig({ preset: "nope" }), /Unknown preset/);
});

test("partial weights merge with the defaults", () => {
  const c = makeConfig({ weights: { interest: 9 } });
  assert.equal(c.weights.interest, 9);
  assert.ok("like_rate" in c.weights);
});

test("blocked words, tags and creators are never ranked", () => {
  const r = rank(vids, { blocked: ["#news"] });
  assert.deepEqual(r.map((s) => s.video.author), ["cats"]);
});

test("custom formulas work and are sandboxed", () => {
  const r = rank(vids, { preset: "formula", formula: '-duration + (5 if has("#cats") else 0)', diversity: 0 });
  assert.equal(r[0].video.author, "cats");
  for (const bad of ['constructor.constructor("return process")()', "this", "x.y", "[1]", "() => 1", "unknown + 1", "1 +"]) {
    assert.throws(() => new Formula(bad), FormulaError, bad);
  }
  assert.equal(new Formula("1 / views").evaluate({}, toVideo("1000000003")), 0); // no division by zero
});

test("learn fits interests and weights from watch history", () => {
  const history = [];
  for (let i = 0; i < 40; i++) {
    const cats = i % 2 === 0;
    const v = toVideo({ id: String(2000000000 + i), author: cats ? "catlady" : "newsdesk", description: cats ? "#cats" : "#news", duration: 10, views: 1000 + i });
    history.push({ video: v, watched: cats ? 10 : 0.5, loops: cats ? 1 : 0, openedComments: false });
  }
  const r = learn(history, {}, null);
  assert.ok(r.weights, r.report.join("\n"));
  assert.ok(r.interests["#cats"] > 0 && r.interests["#news"] < 0);
  assert.ok(r.report.some((l) => l.includes("Fitted on 40 videos")));
});

test("parseExport reads TikTok's JSON export", () => {
  const json = JSON.stringify({
    "Your Activity": {
      "Watch History": { VideoList: [{ Date: "2026-01-01 10:00:00", Link: "https://www.tiktokv.com/share/video/1/" },
                                     { Date: "2026-01-01 10:00:20", Link: "https://www.tiktokv.com/share/video/2/" }] },
      Searches: { SearchList: [{ Date: "2026-01-01 09:00:00", SearchTerm: "pasta recipe" }] },
    },
    "Likes and Favorites": { "Favorite Hashtags": { FavoriteHashtagList: [{ Date: "x", Link: "https://www.tiktok.com/tag/kittens" }] } },
  });
  const { data, seeds, stats } = parseExport("user_data_tiktok.json", json);
  assert.equal(data.watched.length, 2);
  assert.ok(seeds["#kittens"] > 0 && seeds["pasta"] > 0);
  assert.equal(stats.median, 20);
});

test("exportComments makes JSON, CSV and text", () => {
  const v = toVideo({ id: "1000000001", author: "a" });
  const cs = [{ id: "1", author: "bob", text: 'hi, "there"', likes: 3, created: 0 }];
  assert.equal(JSON.parse(exportComments(v, cs, "json")).comments[0].author, "bob");
  assert.ok(exportComments(v, cs, "csv").includes('"hi, ""there"""'));
  assert.ok(exportComments(v, cs, "txt").includes("@bob (3 likes)"));
  assert.throws(() => exportComments(v, cs, "pdf"));
});

test("the public API is complete", () => {
  for (const k of ["mount", "rank", "Formula", "learn", "parseExport", "exportComments", "commentsFrom", "presets", "starterVideos"]) {
    assert.ok(k in MiniTok, k);
  }
  assert.equal(MiniTok.starterVideos.length, 5);
});
