// The club and league marks are decorative, and they are fetched from MLB's
// logo host, so any of them can fail: no internet access, a blocked host, a
// team id the host has no file for. A failed decorative image must leave
// nothing behind rather than a browser's broken-image icon.
//
// This also swaps the club logo as soon as the reader picks a different team,
// so the selector does not describe the previous selection until the form is
// submitted.
(function () {
  "use strict";

  function hide(logo) {
    logo.hidden = true;
  }

  var logos = document.querySelectorAll(".js-logo");
  logos.forEach(function (logo) {
    logo.addEventListener("error", function () {
      hide(logo);
    });
    // A deferred script runs after the images have been requested, so one that
    // already failed will never fire `error` for this listener to catch.
    if (logo.complete && logo.naturalWidth === 0) {
      hide(logo);
    }
  });

  var teamSelect = document.getElementById("team_id");
  var teamLogo = document.getElementById("team-logo");
  if (!teamSelect || !teamLogo) {
    return;
  }

  var prefix = teamLogo.dataset.urlPrefix;
  teamSelect.addEventListener("change", function () {
    teamLogo.hidden = false;
    teamLogo.src = prefix + teamSelect.value + ".svg";
  });
})();
