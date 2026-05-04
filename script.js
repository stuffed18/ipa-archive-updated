(function() {
    // Polyfill for NodeList.forEach (iOS < 9.3)
    if (window.NodeList && !NodeList.prototype.forEach) {
        NodeList.prototype.forEach = Array.prototype.forEach;
    }

    var rawApps = [];
    var apps = []; // Processed app versions
    var baseUrls = {};
    var appsLoaded = false;
    var PER_PAGE = 30;
    var currentPage = 0;
    var currentFiltered = [];
    
    var plistServerUrl = '';
    try {
        plistServerUrl = localStorage.getItem('plistServerUrl') || '';
    } catch (e) {
        console.warn('LocalStorage not accessible (Private Mode?)');
    }

    function escapeHtml(value) {
        var val = (value === null || value === undefined) ? '' : String(value);
        return val
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatOS(min_os) {
        if (!min_os) return 'Unknown';
        var major = Math.floor(min_os / 10000);
        var minor = Math.floor((min_os % 10000) / 100);
        var patch = min_os % 100;
        if (patch > 0) return major + '.' + minor + '.' + patch;
        return major + '.' + minor;
    }

    function parseVersion(v) {
        if (!v) return 0;
        var parts = String(v).split('.').map(function(x) { return parseInt(x) || 0; });
        return (parts[0] || 0) * 10000 + (parts[1] || 0) * 100 + (parts[2] || 0);
    }

    function getPlatformIcons(platform) {
        if (!platform) return '';
        var icons = [];
        if (platform & (1 << 1)) icons.push('<span class="device-icon" title="iPhone"><i class="fa fa-mobile"></i></span>');
        if (platform & (1 << 2)) icons.push('<span class="device-icon" title="iPad"><i class="fa fa-tablet"></i></span>');
        if (platform & (1 << 3)) icons.push('<span class="device-icon" title="Apple TV"><i class="fa fa-television"></i></span>');
        if (platform & (1 << 4)) icons.push('<span class="device-icon" title="Apple Watch"><i class="fa fa-clock-o"></i></span>');
        return '<span class="device-icons">' + icons.join('') + '</span>';
    }

    function getImgPath(image_pk) {
        if (!image_pk) return null;
        return 'data/' + Math.floor(image_pk / 1000) + '/' + image_pk + '.jpg';
    }

    function getAppUrl(app_version) {
        var baseUrl = baseUrls[app_version.base_url_id] || '';
        var path = app_version.path;
        return baseUrl + '/' + path;
    }

    function loadJSON(url, callback) {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, true);
        xhr.onreadystatechange = function() {
            if (xhr.readyState === 4) {
                if (xhr.status === 200) {
                    try {
                        callback(null, JSON.parse(xhr.responseText));
                    } catch (e) {
                        callback(e);
                    }
                } else {
                    callback(new Error('Failed to load: ' + url));
                }
            }
        };
        xhr.send();
    }

    function initData() {
        var statsBar = document.getElementById('statsBar');
        loadJSON('data/urls.json', function(err, urlData) {
            if (err) {
                if (statsBar) statsBar.textContent = 'Error loading URLs';
                return;
            }
            baseUrls = urlData;
            loadJSON('data/ipa.json', function(err, ipaData) {
                if (err) {
                    if (statsBar) statsBar.textContent = 'Error loading Database';
                    return;
                }
                if (statsBar) statsBar.textContent = 'Processing...';
                
                rawApps = ipaData;
                apps = rawApps.map(function(row) {
                    var bundle_id = row[4] || '';
                    return {
                        pk: row[0],
                        platform: row[1],
                        min_os: row[2],
                        title: row[3] || 'Untitled App',
                        bundle_id: bundle_id,
                        version: row[5],
                        base_url_id: row[6],
                        path: row[7].replace(/##/g, '/'),
                        fsize: row[8],
                        image_pk: row[9],
                        icon: getImgPath(row[9]),
                        developer: bundle_id ? bundle_id.split('.').slice(0, 2).join('.') : 'Archive'
                    };
                });

                // Memory Management
                rawApps = null;
                ipaData = null;

                var uniqueBids = {};
                apps.forEach(function(a) {
                    uniqueBids[a.bundle_id || a.title] = true;
                });
                var uniqueCount = Object.keys(uniqueBids).length;
                
                if (statsBar) {
                    statsBar.textContent = apps.length.toLocaleString() + ' IPAs | ' + uniqueCount.toLocaleString() + ' Apps';
                }

                appsLoaded = true;
                loadConfig();
            });
        });
    }

    function renderAppTitle(app) { 
        return escapeHtml(app ? app.title : ''); 
    }

    function getFilteredApps() {
        var q = (document.getElementById('searchInput').value || '').toLowerCase().trim();
        var bid = (document.getElementById('bundleid').value || '').toLowerCase().trim();
        var min = parseVersion(document.getElementById('minos').value);
        var max = parseVersion(document.getElementById('maxos').value) || 999999;
        var devValue = document.getElementById('device').value;
        var dev = devValue ? parseInt(devValue) : 0;
        var unique = document.getElementById('unique').checked;

        var filtered = apps.filter(function(app) {
            if (q && !(app.title.toLowerCase().indexOf(q) > -1 || app.path.toLowerCase().indexOf(q) > -1)) return false;
            if (bid && app.bundle_id.toLowerCase().indexOf(bid) === -1) return false;
            if (min && app.min_os < min) return false;
            if (max && app.min_os > max) return false;
            if (dev && !(app.platform & dev)) return false; 
            return true;
        });

        if (unique) {
            var seen = {};
            var result = [];
            filtered.forEach(function(app) {
                var key = app.bundle_id || app.title;
                if (!seen[key] || app.min_os > seen[key].min_os) {
                    seen[key] = app;
                }
            });
            for (var k in seen) {
                if (seen.hasOwnProperty(k)) result.push(seen[k]);
            }
            filtered = result;
        }
        return filtered;
    }

    window.applyFilters = function(page) {
        if (!appsLoaded) return;
        currentPage = (typeof page === 'number') ? page : 0;
        currentFiltered = getFilteredApps();
        
        var countDiv = document.getElementById('searchResultCount');
        if (countDiv) {
            if (currentFiltered.length > 0) {
                var startRange = (currentPage * PER_PAGE) + 1;
                var endRange = Math.min((currentPage + 1) * PER_PAGE, currentFiltered.length);
                countDiv.textContent = 'Showing ' + startRange.toLocaleString() + '-' + endRange.toLocaleString() + ' of ' + currentFiltered.length.toLocaleString() + ' results';
                countDiv.style.display = 'block';
            } else {
                countDiv.style.display = 'none';
            }
        }

        var searchResults = document.getElementById('searchResults');
        searchResults.className = searchResults.className.replace(/\bresults-fade-in\b/g, '');
        void searchResults.offsetWidth; // Force reflow
        searchResults.className += ' results-fade-in';

        renderGrid(currentFiltered, currentPage);
        renderPagination(currentFiltered.length, currentPage);
        saveConfig();
    };

    function renderPagination(total, page) {
        var nav = document.getElementById('pagination');
        var totalPages = Math.ceil(total / PER_PAGE);
        nav.innerHTML = '';
        
        if (totalPages <= 1) return;

        var wrap = document.createElement('div');
        wrap.className = 'pagination-wrap';

        var range = 2;
        var start = Math.max(0, page - range);
        var end = Math.min(totalPages - 1, page + range);

        var smoothScroll = function() { window.scrollTo(0, 0); };

        if (page > 0) {
            var prev = document.createElement('button');
            prev.innerHTML = '&laquo;';
            prev.onclick = function() { applyFilters(page - 1); smoothScroll(); };
            wrap.appendChild(prev);
        }

        for (var i = start; i <= end; i++) {
            (function(index) {
                var btn = document.createElement('button');
                btn.textContent = index + 1;
                if (index === page) btn.className = 'active';
                btn.onclick = function() { applyFilters(index); smoothScroll(); };
                wrap.appendChild(btn);
            })(i);
        }

        if (page < totalPages - 1) {
            var next = document.createElement('button');
            next.innerHTML = '&raquo;';
            next.onclick = function() { applyFilters(page + 1); smoothScroll(); };
            wrap.appendChild(next);
        }

        nav.appendChild(wrap);
    }

    window.randomIPA = function() {
        if (!appsLoaded) return;

        var searchResults = document.getElementById('searchResults');
        
        // Start Fade Out
        searchResults.className = searchResults.className.replace(/\bresults-fade-in\b/g, '');
        searchResults.className = searchResults.className.replace(/\bresults-fade-out\b/g, '');
        void searchResults.offsetWidth;
        searchResults.className += ' results-fade-out';

        setTimeout(function() {
            document.getElementById('searchInput').value = '';
            document.getElementById('bundleid').value = '';
            currentPage = 0; 
            saveConfig(); 

            var filteredPool = getFilteredApps();
            if (filteredPool.length === 0) {
                alert('No apps match your current filters.');
                searchResults.className = searchResults.className.replace(/\bresults-fade-out\b/g, '');
                return;
            }
            var idx = Math.floor(Math.random() * filteredPool.length);
            var app = filteredPool[idx];
            
            searchResults.innerHTML = '';
            document.getElementById('searchEmptyState').style.display = 'none';
            document.getElementById('pagination').innerHTML = '';

            var container = document.createElement('div');
            container.className = 'hero-random-container';
            
            var card = document.createElement('div');
            card.className = 'app-card-grid-aesthetic hero-card';
            card.innerHTML = '\
                <div class="card-icon-glossy" style="width:120px; height:120px; border-radius:24px;">' +
                    (app.icon ? '<img src="' + app.icon + '" alt="' + escapeHtml(app.title) + '" loading="lazy" onerror="this.onerror=null;this.parentElement.innerHTML=\'<i class=\\\'fa fa-mobile\\\' style=\\\'font-size:40px;\\\'></i>\'">' : '<i class="fa fa-mobile" style="font-size:40px;"></i>') +
                '</div>\
                <div class="card-name-glossy" style="font-size:20px;">' + renderAppTitle(app) + '</div>\
                <div class="card-meta-glossy" style="font-size:14px; margin:10px 0;">\
                    <span class="meta-v">v' + escapeHtml(app.version) + '</span>\
                    <span class="meta-s">' + (app.fsize / 1024).toFixed(1) + ' MB</span>\
                </div>\
                <div class="card-os-glossy" style="font-size:13px; margin-bottom:20px;">\
                    iOS ' + formatOS(app.min_os) + '+\
                    ' + getPlatformIcons(app.platform) + '\
                </div>\
                <button class="ios-btn-blue" style="width:100%; border-radius:20px;" onclick="openModal(\'' + app.pk + '\')">View App</button>';
            container.appendChild(card);
            searchResults.appendChild(container);

            // Trigger Fade In
            searchResults.className = searchResults.className.replace(/\bresults-fade-out\b/g, '');
            void searchResults.offsetWidth;
            searchResults.className += ' results-fade-in';
        }, 300);
    };

    function renderGrid(data, page) {
        var searchResults = document.getElementById('searchResults');
        searchResults.innerHTML = '';
        var slice = data.slice(page * PER_PAGE, (page + 1) * PER_PAGE);
        
        if (slice.length === 0) {
            document.getElementById('searchEmptyState').style.display = 'flex';
            return;
        }
        document.getElementById('searchEmptyState').style.display = 'none';

        slice.forEach(function(app) {
            var card = document.createElement('div');
            card.className = 'app-card-grid-aesthetic';
            card.innerHTML = '\
                <div class="card-icon-glossy">' +
                    (app.icon ? '<img src="' + app.icon + '" alt="' + escapeHtml(app.title) + '" loading="lazy" onerror="this.onerror=null;this.parentElement.innerHTML=\'<i class=\\\'fa fa-mobile\\\'></i>\'">' : '<i class="fa fa-mobile"></i>') +
                '</div>\
                <div class="card-name-glossy">' + renderAppTitle(app) + '</div>\
                <div class="card-meta-glossy">\
                    <span class="meta-v">v' + escapeHtml(app.version) + '</span>\
                    <span class="meta-s">' + (app.fsize / 1024).toFixed(1) + ' MB</span>\
                </div>\
                <div class="card-os-glossy">iOS ' + formatOS(app.min_os) + '+ ' + getPlatformIcons(app.platform) + '</div>\
                <button class="get-btn-glossy" onclick="openModal(\'' + app.pk + '\')">Get</button>';
            searchResults.appendChild(card);
        });
    }

    var VERSIONS_PER_PAGE = 21;

    window.renderVersionPage = function(bundleId, page) {
        var container = document.querySelector('.versions-container[data-bid="' + bundleId + '"]');
        var pagination = document.querySelector('.versions-pagination[data-bid="' + bundleId + '"]');
        if (!container || !pagination) return;

        var allVersions = apps.filter(function(a) { 
            return a.bundle_id === bundleId && a.bundle_id !== ''; 
        }).sort(function(a, b) {
            if (a.min_os !== b.min_os) return a.min_os - b.min_os;
            return String(a.version).localeCompare(b.version, undefined, {numeric: true});
        });
        
        var totalPages = Math.ceil(allVersions.length / VERSIONS_PER_PAGE);
        var slice = allVersions.slice(page * VERSIONS_PER_PAGE, (page + 1) * VERSIONS_PER_PAGE);

        var html = '<ul class="version-list">';
        slice.forEach(function(v) {
            var url = getAppUrl(v);
            var filename = v.path.split('/').pop();
            html += '\
            <li class="version-li">\
                <div class="version-header-row">\
                    <div class="version-icon-mini card-icon-glossy">' +
                        (v.icon ? '<img src="' + v.icon + '" alt="v' + v.version + '" loading="lazy" onerror="this.onerror=null;this.parentElement.innerHTML=\'<i class=\\\'fa fa-mobile\\\'></i>\'">' : '<i class="fa fa-mobile"></i>') +
                    '</div>\
                    <div class="version-info-main">\
                        <div class="version-name"><strong>v' + v.version + '</strong> (' + (v.fsize / 1024).toFixed(1) + ' MB)</div>\
                        <div class="version-filename"><a href="' + url + '" rel="noopener noreferrer nofollow">' + filename + '</a></div>\
                        <div class="version-os">Requires iOS ' + formatOS(v.min_os) + '+</div>\
                    </div>\
                </div>\
                <div class="version-actions-grid" style="grid-template-columns: 1fr 1fr;">\
                    <a href="' + url + '" download class="v-btn-action download-v">Download</a>\
                    <button onclick="installIPA(\'' + v.pk + '\')" class="v-btn-action install-v">Install</button>\
                </div>\
            </li>';
        });
        html += '</ul>';
        container.innerHTML = html;

        pagination.innerHTML = '';
        if (totalPages > 1) {
            var wrap = document.createElement('div');
            wrap.className = 'pagination-wrap';
            
            var range = 2;
            var start = Math.max(0, page - range);
            var end = Math.min(totalPages - 1, page + range);

            if (page > 0) {
                var first = document.createElement('button');
                first.innerHTML = '&laquo;&laquo;';
                first.onclick = function(e) { e.stopPropagation(); renderVersionPage(bundleId, 0); };
                wrap.appendChild(first);

                var prev = document.createElement('button');
                prev.innerHTML = '&laquo;';
                prev.onclick = function(e) { e.stopPropagation(); renderVersionPage(bundleId, page - 1); };
                wrap.appendChild(prev);
            }

            for (var i = start; i <= end; i++) {
                (function(idx) {
                    var btn = document.createElement('button');
                    btn.textContent = idx + 1;
                    if (idx === page) btn.className = 'active';
                    btn.onclick = function(e) { e.stopPropagation(); renderVersionPage(bundleId, idx); };
                    wrap.appendChild(btn);
                })(i);
            }

            if (page < totalPages - 1) {
                var next = document.createElement('button');
                next.innerHTML = '&raquo;';
                next.onclick = function(e) { e.stopPropagation(); renderVersionPage(bundleId, page + 1); };
                wrap.appendChild(next);

                var last = document.createElement('button');
                last.innerHTML = '&raquo;&raquo;';
                last.onclick = function(e) { e.stopPropagation(); renderVersionPage(bundleId, totalPages - 1); };
                wrap.appendChild(last);
            }
            pagination.appendChild(wrap);
        }
    };

    document.querySelectorAll('.segment').forEach(function(seg) {
        seg.addEventListener('click', function() {
            var parent = this.parentElement;
            parent.querySelectorAll('.segment').forEach(function(s) { s.className = s.className.replace(/\bactive\b/g, ''); });
            this.className += ' active';
            
            document.getElementById('device').value = this.getAttribute('data-value');
            applyFilters();
        });
    });

    function setActiveSegment(value) {
        document.querySelectorAll('.segment').forEach(function(s) {
            s.className = s.className.replace(/\bactive\b/g, '');
            if (s.getAttribute('data-value') == (value || '')) {
                s.className += ' active';
            }
        });
    }

    window.openModal = function(pk) {
        var app = apps.filter(function(a) { return a.pk == pk; })[0];
        if (!app) return;
        
        var modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.innerHTML = '\
            <div class="modal-sub-header">\
                <button class="modal-close-btn" onclick="closeModal(this)">Close</button>\
                <div class="modal-sub-header-title">App Info</div>\
                <div style="width:70px;"></div>\
            </div>\
            <div class="modal-content">\
                <div class="modal-app-header">\
                    <div class="modal-app-icon">' + (app.icon ? '<img src="' + app.icon + '" alt="' + escapeHtml(app.title) + '" loading="lazy">' : '<i class="fa fa-mobile"></i>') + '</div>\
                    <div class="modal-app-info">\
                        <div class="modal-app-title">' + escapeHtml(app.title) + '</div>\
                        <div class="modal-app-developer">' + escapeHtml(app.bundle_id) + '</div>\
                    </div>\
                    <button class="random-btn-header" style="width: 80px; height: 32px;" onclick="toggleVersions(this)">Get</button>\
                </div>\
                <div class="modal-section">\
                    <h3><i class="fa fa-mobile"></i> Compatibility</h3>\
                    <p>Requires iOS ' + formatOS(app.min_os) + ' or later.</p>\
                    <p>Devices: ' + getPlatformIcons(app.platform) + '</p>\
                </div>\
                <div class="modal-section">\
                    <h3><i class="fa fa-wrench"></i> Install on device</h3>\
                    <p style="font-size:12px; color:#666; margin-bottom:10px;">Configure your plist server below.</p>\
                    <button onclick="document.getElementById(\'plistConfigArea\').style.display=\'flex\'" class="get-btn-glossy" style="padding:6px 12px; font-size:12px; margin-bottom:10px;">Configure now</button>\
                    <div id="plistConfigArea" class="plist-config-row" style="display: ' + (plistServerUrl ? 'flex' : 'none') + '; flex-wrap: wrap;">\
                        <input type="text" id="plistServerInput" value="' + plistServerUrl + '" placeholder="http://192.168.0.1/" class="ios-input" style="font-size:12px; padding:6px 10px; flex: 1; min-width: 180px;">\
                        <button onclick="savePlistServer()" class="get-btn-glossy" style="padding:6px 12px; font-size:12px; background: #0a84ff; color: white;">Save</button>\
                    </div>\
                </div>\
                <div class="version-sheet-overlay" onclick="if(event.target === this) this.className = this.className.replace(/\\bactive\\b/g, \'\')">\
                    <div class="version-sheet">\
                        <h3 class="version-sheet-title">Version History</h3>\
                        <div class="versions-container" data-bid="' + app.bundle_id + '"></div>\
                        <div class="versions-pagination ios-pagination" style="margin-top:15px;" data-bid="' + app.bundle_id + '"></div>\
                    </div>\
                </div>\
            </div>';
        
        document.getElementById('modalContainer').appendChild(modal);
        document.body.style.overflow = 'hidden';

        setTimeout(function() {
            modal.className += ' active';
            renderVersionPage(app.bundle_id, 0);
        }, 10);
    };

    window.savePlistServer = function() {
        var val = document.getElementById('plistServerInput').value.trim();
        if (val && val.indexOf('http') !== 0) { alert('URL must start with http:// or https://'); return; }
        plistServerUrl = val;
        try {
            localStorage.setItem('plistServerUrl', val);
        } catch (e) {
            alert('Cannot save settings: LocalStorage is disabled or full (Private Mode?)');
        }
        alert('Settings saved!');
    };

    window.installIPA = function(pk) {
        if (!plistServerUrl) {
            alert('Please configure a Plist Server URL.');
            return;
        }
        var app = apps.filter(function(a) { return a.pk == pk; })[0];
        if (!app) return;

        var thisServerUrl = location.href.split('#')[0].split('?')[0];
        if (thisServerUrl.slice(-1) !== '/') thisServerUrl += '/';

        var data = {
            u: getAppUrl(app),
            n: app.title,
            b: app.bundle_id,
            v: app.version.split(' ')[0],
            i: thisServerUrl + app.icon
        };
        
        var json = JSON.stringify(data);
        var b64 = '';
        try {
            b64 = btoa(unescape(encodeURIComponent(json)));
        } catch (e) {
            b64 = btoa(json);
        }
        b64 = b64.replace(/=/g, '');
        var sep = plistServerUrl.indexOf('?') > -1 ? '&' : '?';
        var plistUrl = plistServerUrl + sep + 'd=' + b64;
        window.open('itms-services://?action=download-manifest&url=' + encodeURIComponent(plistUrl));
    };

    window.closeModal = function(btn) {
        var modal = btn.parentNode.parentNode; // modal-overlay
        modal.className = modal.className.replace(/\bactive\b/g, '');
        document.body.style.overflow = 'auto';
        setTimeout(function() { modal.parentNode.removeChild(modal); }, 400);
    };

    window.toggleVersions = function(btn) {
        var sheet = btn.parentNode.parentNode.querySelector('.version-sheet-overlay');
        if (sheet.className.indexOf('active') > -1) {
            sheet.className = sheet.className.replace(/\bactive\b/g, '');
        } else {
            sheet.className += ' active';
        }
    };

    function saveConfig() {
        var data = {
            q: document.getElementById('searchInput').value,
            bid: document.getElementById('bundleid').value,
            min: document.getElementById('minos').value,
            max: document.getElementById('maxos').value,
            dev: document.getElementById('device').value,
            uni: document.getElementById('unique').checked,
            p: currentPage
        };
        var params = [];
        for (var k in data) {
            if (data.hasOwnProperty(k) && data[k]) {
                params.push(k + '=' + encodeURIComponent(data[k]));
            }
        }
        window.history.replaceState({}, '', window.location.pathname + '#' + params.join('&'));
    }

    function loadConfig() {
        if (!location.hash) return;
        var params = {};
        location.hash.substring(1).split('&').forEach(function(pair) {
            var parts = pair.split('=');
            params[parts[0]] = decodeURIComponent(parts[1]);
        });
        
        if (params.q) document.getElementById('searchInput').value = params.q;
        if (params.bid) document.getElementById('bundleid').value = params.bid;
        if (params.min) document.getElementById('minos').value = params.min;
        if (params.max) document.getElementById('maxos').value = params.max;
        if (params.dev) {
            document.getElementById('device').value = params.dev;
            setActiveSegment(params.dev);
        }
        if (params.uni) document.getElementById('unique').checked = (params.uni !== 'false');
        currentPage = parseInt(params.p) || 0;
        
        if (location.hash.length > 2) applyFilters(currentPage);
    }

    // Start
    initData();
})();
