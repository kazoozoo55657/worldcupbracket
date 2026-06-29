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
      var p = [winners[f[0]] || null, winners[f[1]] || null];
      participants[no] = p;
      winners[no] = chooseWinner(no, p);
    }
  }

  function teamHtml(no, teamId, label, winnerId, disabled) {
    if (!teamId) return '<div class="kteam"><span class="ktbd">' + esc(label) + "</span></div>";
    var t = TEAMS[teamId] || { name: "?", flag: "", medal: null };
    var win = winnerId === teamId;
    return (
      '<div class="kteam' + (win ? " win" : "") + '"><label class="kpick">' +
      '<input type="radio" name="win_' + no + '" value="' + teamId + '"' +
      (win ? " checked" : "") + (disabled ? " disabled" : "") + ">" +
      flagHtml(t) + '<span class="ktn">' + esc(t.name) + "</span>" + starHtml(t.medal) +
      "</label></div>"
    );
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
      el.className = "kmatch" + (w ? " done" : "") + (locked ? " locked" : "");
      el.innerHTML =
        '<span class="kmno" title="Official match ' + no + '">M' + no + "</span>" +
        teamHtml(no, pair[0], lab.home, w, disabled) +
        teamHtml(no, pair[1], lab.away, w, disabled) +
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
