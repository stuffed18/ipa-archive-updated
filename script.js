var DB = [];
var DB_result = [];
var baseUrls = {};
var PER_PAGE = 30;
var isInitial = true;
var previousSearch = '';
var plistServerUrl = ''; // will append ?d=<data>
NodeList.prototype.forEach = Array.prototype.forEach; // fix for < iOS 9.3

/*
 * Init
 */

function setMessage(msg) {
    document.getElementById('content').innerHTML = msg;
}

function loadFile(url, onErrFn, fn, onProgress) {
    try {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, true);
        xhr.onload = function () { fn(xhr.responseText); };
        xhr.onerror = function (e) { onErrFn('Server or network error.'); };
        if (onProgress) {
            xhr.onprogress = function (e) {
                onProgress(e.loaded, e.total, e.lengthComputable);
            };
        }
        xhr.send();
    } catch (error) {
        onErrFn(error);
    }
}

function loadDB() {
    var config = null;
    try {
        config = loadConfig(true);
    } catch (error) {
        alert(error);
    }
    setMessage('Loading database ...');
    var urlsData = null;
    var ipaData = null;
    var urlsLoaded = false;
    var ipaLoaded = false;

    function parseWhenReady() {
        if (!urlsLoaded || !ipaLoaded) { return; }
        setMessage('Parsing database ...');
        window.setTimeout(function () {
            try {
                baseUrls = JSON.parse(urlsData);
                urlsData = null;
                DB = JSON.parse(ipaData);
                ipaData = null;
                setMessage('ready. Links in database: ' + DB.length);
                if (config && (config.page > 0 || config.search || config.bundleid)) {
                    searchIPA(true);
                }
            } catch (error) {
                setMessage('Could not read database: ' + error);
            }
        }, 0);
    }

    loadFile('data/urls.json', setMessage, function (data) {
        urlsData = data;
        urlsLoaded = true;
        parseWhenReady();
    });
    loadFile('data/ipa.json', setMessage, function (data) {
        ipaData = data;
        ipaLoaded = true;
        parseWhenReady();
    }, function (loaded, total, lengthComputable) {
        if (lengthComputable && total > 0) {
            setMessage('Loading database ... ' + Math.floor(loaded * 100 / total) + '%');
        } else {
            setMessage('Loading database ... ' + Math.floor(loaded / 1048576) + ' MB');
        }
    });
}

function loadConfig(chkServer) {
    if (!location.hash) {
        return; // keep default values
    }
    var params = location.hash.substring(1).split('&');
    var data = {};
    params.forEach(function (param) {
        var pair = param.split('=', 2);
        data[pair[0]] = decodeURIComponent(pair[1]);
    });
    document.querySelectorAll('input,select').forEach(function (input) {
        if (input.type === 'checkbox') {
            input.checked = data[input.id] || null;
        } else {
            input.value = data[input.id] || '';
        }
    });
    if (chkServer && data['plistServer']) {
        setPlistGen();
    }
    return data;
}

function saveConfig() {
    var data = [];
    document.querySelectorAll('input,select').forEach(function (e) {
        var value = e.type === 'checkbox' ? e.checked : e.value;
        if (value) {
            data.push(e.id + '=' + encodeURIComponent(value));
        }
    });
    var prev = location.hash;
    location.hash = '#' + data.join('&');
    return prev !== location.hash;
}

/*
 * Search
 */

function applySearch() {
    var rawTerm = (document.getElementById('search').value || '').trim();
    var term = rawTerm.toLowerCase();
    var cleanTerm = term.replace(/[\s\-_.]+/g, '');
    var words = term.split(/\s+/).filter(function (w) { return w.length > 0; });
    var hasSpecificVersion = words.some(function (w) { return /^[vV]?[0-9]+(\.[0-9]+)+$/.test(w); });

    var bundle = document.getElementById('bundleid').value.trim().toLowerCase();
    var unique = document.getElementById('unique').checked;
    var minos = document.getElementById('minos').value;
    var maxos = document.getElementById('maxos').value;
    var platform = document.getElementById('device').value;
    var minid = document.getElementById('minid').value;

    var minV = minos ? strToVersion(minos) : 0;
    var maxV = maxos ? strToVersion(maxos) : 9999999;
    var device = platform ? 1 << platform : 255; // all flags
    var minPK = minid ? parseInt(minid) : 0;

    // [7, 2,20200,"180","com.headcasegames.180","1.0",1,"180.ipa", 189930], 
    // [pk, platform, minOS, title, bundleId, version, baseUrl, pathName, size]
    DB_result = [];
    isInitial = false;
    var uniqueBundleIds = {};

    if (!term) {
        DB.forEach(function (ipa, i) {
            if (ipa[2] < minV || ipa[2] > maxV || !(ipa[1] & device) || ipa[0] < minPK) {
                return;
            }
            if (bundle && ipa[4].toLowerCase().indexOf(bundle) === -1) {
                return;
            }
            if (unique) {
                var bId = ipa[4];
                if (uniqueBundleIds[bId]) {
                    return;
                }
                uniqueBundleIds[bId] = true;
            }
            DB_result.push(i);
        });
        delete uniqueBundleIds;
        return;
    }

    // Pass 1: Find direct matches and collect matching bundle IDs
    var matchingBundles = {};
    var directMatches = [];

    for (var i = 0; i < DB.length; i++) {
        var ipa = DB[i];
        var titleLower = (ipa[3] || '').toLowerCase();
        var bundleLower = (ipa[4] || '').toLowerCase();
        var pathLower = (ipa[7] || '').toLowerCase();

        var matched = false;

        // 1. Direct exact substring match
        if (titleLower.indexOf(term) > -1 || bundleLower.indexOf(term) > -1 || pathLower.indexOf(term) > -1) {
            matched = true;
        }
        // 2. Space / punctuation-insensitive match (e.g. "alive4ever" <=> "alive 4 ever")
        else if (cleanTerm.length >= 2) {
            var cleanTitle = titleLower.replace(/[\s\-_.]+/g, '');
            var cleanBundle = bundleLower.replace(/[\s\-_.]+/g, '');
            var cleanPath = pathLower.replace(/[\s\-_.]+/g, '');
            if (cleanTitle.indexOf(cleanTerm) > -1 || cleanBundle.indexOf(cleanTerm) > -1 || cleanPath.indexOf(cleanTerm) > -1) {
                matched = true;
            }
        }

        // 3. Multi-word search (all words must appear in title, bundle, or path)
        if (!matched && words.length > 1) {
            matched = words.every(function (w) {
                return titleLower.indexOf(w) > -1 || bundleLower.indexOf(w) > -1 || pathLower.indexOf(w) > -1;
            });
        }

        if (matched) {
            directMatches[i] = 1;
            if (!hasSpecificVersion && ipa[4] && ipa[4].length > 3) {
                matchingBundles[ipa[4]] = true;
            }
        }
    }

    // Pass 2: Filter by criteria (minOS, maxOS, device, unique, etc.) and include bundle-expanded entries
    for (var i = 0; i < DB.length; i++) {
        var ipa = DB[i];
        if (ipa[2] < minV || ipa[2] > maxV || !(ipa[1] & device) || ipa[0] < minPK) {
            continue;
        }
        if (bundle && ipa[4].toLowerCase().indexOf(bundle) === -1) {
            continue;
        }

        var isMatch = (directMatches[i] === 1) || (!hasSpecificVersion && ipa[4] && matchingBundles[ipa[4]]);
        if (!isMatch) {
            continue;
        }

        if (unique) {
            var bId = ipa[4];
            if (uniqueBundleIds[bId]) {
                continue;
            }
            uniqueBundleIds[bId] = true;
        }
        DB_result.push(i);
    }
    delete uniqueBundleIds;
}

function restoreSearch() {
    location.hash = previousSearch;
    var conf = loadConfig(false);
    previousSearch = '';
    if (conf.random) {
        randomIPA(conf.random);
    } else {
        searchIPA(true);
    }
}

function searchBundle(idx, additional) {
    previousSearch = location.hash + (additional || '');
    document.getElementById('bundleid').value = DB[idx][4];
    document.getElementById('search').value = '';
    document.getElementById('page').value = null;
    document.getElementById('unique').checked = false;
    searchIPA();
}

function searchIPA(restorePage) {
    var page = 0;
    if (restorePage) {
        page = document.getElementById('page').value;
    } else {
        document.getElementById('page').value = null;
    }
    applySearch();
    printIPA((page || 0) * PER_PAGE);
    saveConfig();
}

/*
 * Random IPA
 */

function urlsToImgs(redirectUrl, list) {
    var template = getTemplate('.screenshot');
    var rv = '<div class="carousel">';
    for (var i = 0; i < list.length; i++) {
        rv += renderTemplate(template, { $REF: list[i], $URL: redirectUrl + list[i] }).replace(/XSRC=/gi, 'src=');
    }
    return rv + '</div>';
}

function randomIPA(specificId) {
    document.getElementById('search').value = '';
    document.getElementById('bundleid').value = '';
    if (saveConfig() || isInitial || specificId) {
        applySearch();
    }
    var idx = specificId;
    if (!specificId) {
        if (DB_result.length > 0) {
            idx = DB_result[Math.floor(Math.random() * DB_result.length)];
        } else {
            idx = Math.floor(Math.random() * DB.length);
        }
    }
    var entry = entryToDict(DB[idx]);
    var output = document.getElementById('content');
    output.innerHTML = '<h3>Random:</h3>' + entriesToStr('.full', [idx]);
    output.lastElementChild.className += ' single';
    output.innerHTML += renderTemplate(getTemplate('.randomAction'), { $IDX: idx });

    if (!plistServerUrl) {
        output.innerHTML += getTemplate('.no-itunes');
        return;
    }
    // Append iTunes info to result
    var redirectUrl = plistServerUrl + '?r='
    var iTunesUrl = 'https://itunes.apple.com/lookup?bundleId=' + entry.bundleId;
    loadFile(redirectUrl + iTunesUrl, console.error, function (data) {
        var obj = JSON.parse(data);
        if (!obj || obj.resultCount < 1) {
            output.innerHTML += '<p class="no-itunes">No iTunes results.</p>';
            return;
        }
        var info = obj.results[0];
        var imgs1 = info.screenshotUrls;
        var imgs2 = info.ipadScreenshotUrls;
        var device = document.getElementById('device').value || 255;

        var imgStr = '';
        if (imgs1 && imgs1.length > 0 && device & 1) {
            imgStr += '<p>iPhone Screenshots:</p>' + urlsToImgs(redirectUrl, imgs1);
        }
        if (imgs2 && imgs2.length > 0 && device & 2) {
            imgStr += '<p>iPad Screenshots:</p>' + urlsToImgs(redirectUrl, imgs2);
        }

        output.innerHTML += renderTemplate(getTemplate('.itunes'), {
            $VERSION: info.version,
            $PRICE: info.formattedPrice,
            $RATING: info.averageUserRating.toFixed(1),
            $ADVISORY: info.contentAdvisoryRating,
            $DATE: info.currentVersionReleaseDate,
            $GENRES: (info.genres || []).join(', '),
            $URL: info.trackViewUrl,
            $IMG: imgStr,
            $DESCRIPTION: info.description,
        });
    });
}

/*
 * Output
 */

function platformToStr(num) {
    if (!num) { return '?'; }
    return [
        num & (1 << 1) ? 'iPhone' : null,
        num & (1 << 2) ? 'iPad' : null,
        num & (1 << 3) ? 'TV' : null,
        num & (1 << 4) ? 'Watch' : null,
    ].filter(Boolean).join(', ');
}

function versionToStr(num) {
    if (!num) { return '?'; }
    var major = Math.floor(num / 10000);
    var minor = Math.floor(num / 100) % 100;
    var patch = num % 100;
    return major + '.' + minor + (patch ? '.' + patch : '');
}

function strToVersion(versionStr) {
    var x = ((versionStr || '0') + '.0.0.0').split('.');
    return parseInt(x[0]) * 10000 + parseInt(x[1]) * 100 + parseInt(x[2]);
}

function humanSize(size) {
    var sizeIndex = 0;
    while (size > 1024) {
        size /= 1024;
        sizeIndex += 1;
    }
    return size.toFixed(1) + ['kB', 'MB', 'GB'][sizeIndex];
}

function getTemplate(name) {
    return document.getElementById('templates').querySelector(name).outerHTML;
}

function renderTemplate(template, values) {
    return template.replace(/\$[A-Z]+/g, function (x) { return values[x]; });
}

function validUrl(url) {
    return encodeURI(url).replace('#', '%23').replace('?', '%3F');
}

function entryToDict(entry) {
    var pk = entry[0];
    var img_pk = (entry[9] !== undefined && entry[9] !== null) ? entry[9] : pk;
    return {
        pk: pk,
        platform: entry[1],
        minOS: entry[2],
        title: entry[3],
        bundleId: entry[4],
        version: entry[5],
        baseUrl: entry[6],
        pathName: entry[7],
        size: entry[8],
        ipa_url: baseUrls[entry[6]] + '/' + entry[7],
        img_url: 'data/' + Math.floor(img_pk / 1000) + '/' + img_pk + '.jpg?v=2',
    }
}

function onImgError(img) {
    img.onerror = null;
    img.src = 'apple-touch-icon.png';
}

function entriesToStr(templateType, data) {
    var template = getTemplate(templateType);
    var rv = '';
    for (var i = 0; i < data.length; i++) {
        var entry = entryToDict(DB[data[i]]);
        rv += renderTemplate(template, {
            $IDX: data[i],
            $IMG: entry.img_url,
            $TITLE: (entry.title || '?').replace('<', '&lt;'),
            $VERSION: entry.version,
            $BUNDLEID: entry.bundleId,
            $MINOS: versionToStr(entry.minOS),
            $PLATFORM: platformToStr(entry.platform),
            $SIZE: humanSize(entry.size),
            $URLNAME: entry.pathName.split('/').slice(-1), // decodeURI
            $URL: validUrl(entry.ipa_url),
        }).replace(/XSRC=/gi, 'src=');
    }
    return rv;
}

function printIPA(offset) {
    if (!offset) { offset = 0; }

    var total = DB_result.length;
    var content = '<h3>Results: ' + total;
    if (previousSearch) {
        content += ' -- Go to: <a onclick="restoreSearch()">previous search</a>';
    }
    content += '</h3>';
    var page = Math.floor(offset / PER_PAGE);
    var pages = Math.ceil(total / PER_PAGE);
    if (pages > 1) {
        content += paginationShort(page, pages);
    }

    var templateType = document.getElementById('unique').checked ? '.short' : '.entry';
    content += entriesToStr(templateType, DB_result.slice(offset, offset + PER_PAGE));

    if (pages > 1) {
        content += paginationShort(page, pages);
        content += paginationFull(page, pages);
    }

    document.getElementById('content').innerHTML = content;
    window.scrollTo(0, 0);
}

/*
 * Pagination
 */

function p(page) {
    printIPA(page * PER_PAGE);
    document.getElementById('page').value = page || null;
    saveConfig();
}

function paginationShort(page, pages) {
    return '<div class="shortpage">'
        + '<button onclick="p(' + (page - 1) + ')" ' + (page == 0 ? 'disabled' : '') + '>Prev</button>'
        + '<span>' + (page + 1) + ' / ' + pages + '</span>'
        + '<button onclick="p(' + (page + 1) + ')" ' + (page + 1 == pages ? 'disabled' : '') + '>Next</button>'
        + '</div>';
}

function paginationFull(page, pages) {
    var rv = '<div id="pagination">Pages:';
    for (var i = 0; i < pages; i++) {
        if (i === page) {
            rv += '\n<b>' + (i + 1) + '</b>';
        } else {
            rv += '\n<a onclick="p(' + i + ')">' + (i + 1) + '</a>';
        }
    }
    return rv + '</div>';
}

/*
 * Install on iDevice
 */

function setPlistGen() {
    var testURL = document.getElementById('plistServer').value;
    var scheme = testURL.slice(0, 7);
    if (scheme != 'http://' && scheme != 'https:/') {
        alert('URL must start with http:// or https://.');
        return;
    }
    loadFile(testURL + '?d=' + btoa('{"u":"1"}'), alert, function (data) {
        if (data.trim().slice(0, 6) != '<?xml ') {
            alert('Server did not respond with a Plist file.');
            return;
        }
        plistServerUrl = testURL;
        document.getElementById('overlay').hidden = true;
        saveConfig();
    });
}

function urlWithSlash(url) {
    return url.toString().slice(-1) === '/' ? url : (url + '/');
}

function utoa(data) {
    return btoa(unescape(encodeURIComponent(data)));
}

function installIPA(idx) {
    if (!plistServerUrl) {
        document.getElementById('overlay').hidden = false;
        return;
    }
    var thisServerUrl = location.href.replace(location.hash, '');
    var entry = entryToDict(DB[idx]);
    var json = JSON.stringify({
        u: validUrl(entry.ipa_url),
        n: entry.title,
        b: entry.bundleId,
        v: entry.version.split(' ')[0],
        i: urlWithSlash(thisServerUrl) + entry.img_url,
    }, null, 0)
    var b64 = '';
    try {
        b64 = btoa(json);
    } catch (error) {
        b64 = utoa(json);
    }
    while (b64.slice(-1) === '=') {
        b64 = b64.slice(0, -1);
    }
    // window.open(plistServerUrl + '?d=' + b64);
    var plistUrl = plistServerUrl + '%3Fd%3D' + b64; // url encoded "?d="
    window.open('itms-services://?action=download-manifest&url=' + plistUrl);
}
