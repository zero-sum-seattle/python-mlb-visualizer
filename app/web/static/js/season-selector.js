// Keeps the season selector in step with the team selector so the form cannot
// submit a season the chosen team has no games for. The server still validates
// the combination; this only fixes the normal click-through path.
(function () {
  "use strict";

  var catalogElement = document.getElementById("team-seasons-data");
  var teamSelect = document.getElementById("team_id");
  var seasonSelect = document.getElementById("season");
  if (!catalogElement || !teamSelect || !seasonSelect) {
    return;
  }

  var seasonsByTeam;
  try {
    seasonsByTeam = JSON.parse(catalogElement.textContent);
  } catch (error) {
    return;
  }

  teamSelect.addEventListener("change", function () {
    var seasons = seasonsByTeam[teamSelect.value];
    if (!seasons || seasons.length === 0) {
      return;
    }

    var options = document.createDocumentFragment();
    seasons.forEach(function (season, index) {
      var option = document.createElement("option");
      option.value = String(season);
      option.textContent = String(season);
      // Seasons arrive newest first, so the first one is the default.
      option.selected = index === 0;
      options.appendChild(option);
    });

    seasonSelect.replaceChildren(options);
  });
})();
