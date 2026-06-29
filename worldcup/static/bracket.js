// Live knockout autofill. Picking a match winner cascades that team into the next
// round instantly — no "Save bracket" round-trip needed. The whole knockout tree is
// recomputed from one source of truth (the real R32 field + the member's per-match
// winner choices), exactly mirroring the server's bracket_structure.resolve so that
// saving produces the same result. Games whose real fixture has kicked off arrive
// pre-locked from the server and can't be changed here.
(function () {
  "use strict";

  var blob = document.getElementById("ko-data");
  var form = document.getElementById("bform");
  if (!blob || !form) return;

  var DATA = JSON.parse(blob.textContent || "{}");
  var TEAMS = DATA.teams || {};
  var FINAL_NO = DATA.final_no;
  var REAL = DATA.real_winners || {}; // {matchNo: teamId that really won}
  var SCORES = DATA.scores || {};     // {matchNo: {teamId: score}}
  var CHOICES = Object.assign({}, DATA.choices); // {matchNo: teamId}

  var participants = {}; // {matchNo: [homeId|null, awayId|null]}
  var winners = {};      // {matchNo: teamId|null}

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function flagHtml(t) {
    return t && t.flag ? '<img class="flag" src="' + esc(t.flag) + '" alt="" loading="lazy">' : "";
  }
  function starHtml(medal) {
    return medal ? '<span class="star ' + esc(medal) + '">★</span>' : "";
  }

  // A submitted winner counts only if it's actually one of the match's two
  // participants, so changing an upstream pick auto-invalidates stale downstream ones.
  function chooseWinner(no, pair) {
    var c = CHOICES[no];
    return c && (c === pair[0] || c === pair[1]) ? c : null;
  }

  function recompute() {
    participants = {};
    winners = {};
    var no;
    for (no in DATA.r32) {
      var pair = DATA.r32[no].slice();
      participants[no] = pair;
      winners[no] = chooseWinner(no, pair);
    }
    var fedNos = Object.keys(DATA.feeds).map(Number).sort(function (a, b) { return a - b; });
    for (var i = 0; i < fedNos.length; i++) {
      no = fedNos[i];
      var f = DATA.feeds[no];
      // Slot = member's predicted winner of the feeding game, or — if they never
      // picked it and it's already decided — the real winner, so later rounds stay
      // pickable after a missed early game. Mirrors bracket_structure.resolve.
      var p = [winners[f[0]] || REAL[f[0]] || null, winners[f[1]] || REAL[f[1]] || null];
      participants[no] = p;
      winners[no] = chooseWinner(no, p);
    }
  }

  function scoreHtml(matchScores, teamId) {
    return teamId in matchScores ? '<span class="kscore">' + esc(matchScores[teamId]) + "</span>" : "";
  }

  function teamHtml(no, teamId, label, winnerId, disabled, actualId) {
    if (!teamId) return '<div class="kteam"><span class="ktbd">' + esc(label) + "</span></div>";
    var t = TEAMS[teamId] || { name: "?", flag: "" };
    var sc = SCORES[no] || {};
    // The slot's predicted occupant is wrong if the feeding game is decided and a
    // different team really advanced. A pick is "correct" only once the real game is
    // played and that team actually won it — that's the only green (the check).
    var wrong = actualId && actualId !== teamId;
    var picked = winnerId === teamId && !wrong;
    var correct = picked && REAL[no] === teamId;
    var pick =
      '<label class="kpick">' +
      '<input type="radio" name="win_' + no + '" value="' + teamId + '"' +
      (winnerId === teamId ? " checked" : "") + (disabled ? " disabled" : "") + ">" +
      flagHtml(t) + '<span class="ktn">' + esc(t.name) + "</span>" +
      (correct ? '<span class="kcheck" title="Correct pick">✓</span>' : "") +
      scoreHtml(sc, teamId) +
      "</label>";
    if (wrong) {
      var at = TEAMS[actualId] || { name: "?", flag: "" };
      return (
        '<div class="kteam wrong"><span class="kactual">' +
        flagHtml(at) + '<span class="ktn">' + esc(at.name) + "</span>" +
        '<span class="kcheck" title="Actually advanced">✓</span>' + scoreHtml(sc, actualId) +
        "</span>" + pick + "</div>"
      );
    }
    return '<div class="kteam' + (picked ? " picked" : "") + (correct ? " correct" : "") + '">' + pick + "</div>";
  }

  function render() {
    var allNos = Object.keys(DATA.r32).concat(Object.keys(DATA.feeds));
    for (var i = 0; i < allNos.length; i++) {
      var no = allNos[i];
      var el = document.getElementById("m" + no);
      if (!el) continue;
      var pair = participants[no] || [null, null];
      var w = winners[no] || null;
      var locked = !!DATA.locks[no];
      var bothReady = pair[0] && pair[1];
      var disabled = locked || !bothReady;
      var lab = DATA.labels[no] || { home: "", away: "" };
      // For matches fed by earlier games, the real occupant of each slot is the
      // winner of its feeding game (R32 slots are the real field, so no feeds).
      var feeds = DATA.feeds[no];
      var aHome = feeds ? (REAL[feeds[0]] || null) : null;
      var aAway = feeds ? (REAL[feeds[1]] || null) : null;
      el.className = "kmatch" + (w ? " done" : "") + (locked ? " locked" : "");
      el.innerHTML =
        '<span class="kmno" title="Official match ' + no + '">M' + no + "</span>" +
        teamHtml(no, pair[0], lab.home, w, disabled, aHome) +
        teamHtml(no, pair[1], lab.away, w, disabled, aAway) +
        '<span class="kconn" aria-hidden="true"></span>' +
        (locked ? '<span class="klock" title="This game has been played — picks are locked">🔒 played</span>' : "");
    }
    var champWrap = document.getElementById("champ-wrap");
    if (champWrap) {
      var champ = winners[FINAL_NO] || null;
      champWrap.innerHTML = champ
        ? '· <span class="champ">🏆 ' + flagHtml(TEAMS[champ]) + esc(TEAMS[champ].name) + "</span>"
        : "";
    }
  }

  form.addEventListener("change", function (e) {
    var el = e.target;
    if (!el.name || el.name.indexOf("win_") !== 0) return;
    if (el.checked) CHOICES[Number(el.name.slice(4))] = Number(el.value);
    recompute();
    render();
  });

  recompute();
  render();
})();
