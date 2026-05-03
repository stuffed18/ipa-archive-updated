        let rawApps = [];
        let apps = []; // Processed app versions
        let baseUrls = {};
        let appsLoaded = false;
        let PER_PAGE = 30;
        let currentPage = 0;
        let currentFiltered = [];

        function escapeHtml(value) {
            return String(value ?? '')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
        }

        function formatOS(min_os) {
            if (!min_os) return 'Unknown';
            const major = Math.floor(min_os / 10000);
            const minor = Math.floor((min_os % 10000) / 100);
            const patch = min_os % 100;
            if (patch > 0) return `${major}.${minor}.${patch}`;
            return `${major}.${minor}`;
        }

        function parseVersion(v) {
            if (!v) return 0;
            const parts = String(v).split('.').map(x => parseInt(x) || 0);
            return (parts[0] || 0) * 10000 + (parts[1] || 0) * 100 + (parts[2] || 0);
        }

        function getPlatformIcons(platform) {
            if (!platform) return '';
            const icons = [];
            if (platform & (1 << 1)) icons.push('<span class="device-icon" title="iPhone"><i class="fa-solid fa-mobile-screen-button"></i></span>');
            if (platform & (1 << 2)) icons.push('<span class="device-icon" title="iPad"><i class="fa-solid fa-tablet-screen-button"></i></span>');
            if (platform & (1 << 3)) icons.push('<span class="device-icon" title="Apple TV"><i class="fa-solid fa-tv"></i></span>');
            if (platform & (1 << 4)) icons.push('<span class="device-icon" title="Apple Watch"><i class="fa-solid fa-watch"></i></span>');
            return `<span class="device-icons">${icons.join('')}</span>`;
        }

        function getImgPath(image_pk) {
            if (!image_pk) return null;
            return `data/${Math.floor(image_pk / 1000)}/${image_pk}.jpg`;
        }

        let plistServerUrl = localStorage.getItem('plistServerUrl') || '';

        function getAppUrl(app_version) {
            const baseUrl = baseUrls[app_version.base_url_id] || '';
            const path = app_version.path;
            return baseUrl + '/' + path; // Raw path for maximum server compatibility
        }

        async function fetchAppsData() {
            const statsBar = document.getElementById('statsBar');
            const [urlRes, ipaRes] = await Promise.all([
                fetch('data/urls.json'),
                fetch('data/ipa.json')
            ]);
            
            baseUrls = await urlRes.json();
            rawApps = await ipaRes.json();
            
            // Map raw rows to objects for easier filtering
            apps = rawApps.map(row => ({
                pk: row[0],
                platform: row[1],
                min_os: row[2],
                title: row[3] || 'Untitled App',
                bundle_id: row[4] || '',
                version: row[5],
                base_url_id: row[6],
                path: row[7].replaceAll('##', '/'),
                fsize: row[8],
                image_pk: row[9],
                icon: getImgPath(row[9]),
                developer: row[4] ? row[4].split('.').slice(0, 2).join('.') : 'Archive'
            }));

            // Calculate unique app count
            const uniqueBids = new Set(apps.map(a => a.bundle_id || a.title));
            if (statsBar) statsBar.textContent = `${apps.length.toLocaleString()} IPAs | ${uniqueBids.size.toLocaleString()} Apps`;

            appsLoaded = true;
            return apps;
        }

        const appsPromise = fetchAppsData();

        function renderAppTitle(app) { return `${escapeHtml(app?.title)}`; }
        function renderAppTitleWithDevices(app) { return `${escapeHtml(app?.title)}${getPlatformIcons(app?.platform)}`; }

        // DOM Elements
        const searchInput = document.getElementById('searchInput');
        const bundleInput = document.getElementById('bundleid');
        const minosInput = document.getElementById('minos');
        const maxosInput = document.getElementById('maxos');
        const deviceSelect = document.getElementById('device');
        const uniqueCheck = document.getElementById('unique');
        const searchResults = document.getElementById('searchResults');
        const emptyState = document.getElementById('searchEmptyState');
        const modalContainer = document.getElementById('modalContainer');

        function getFilteredApps() {
            const q = searchInput.value.toLowerCase().trim();
            const bid = bundleInput.value.toLowerCase().trim();
            const min = parseVersion(minosInput.value);
            const max = parseVersion(maxosInput.value) || 999999;
            const devValue = deviceSelect.value;
            const dev = devValue ? parseInt(devValue) : 0;
            const unique = uniqueCheck.checked;

            let filtered = apps.filter(app => {
                if (q && !(app.title.toLowerCase().includes(q) || app.path.toLowerCase().includes(q))) return false;
                if (bid && !app.bundle_id.toLowerCase().includes(bid)) return false;
                if (min && app.min_os < min) return false;
                if (max && app.min_os > max) return false;
                if (dev && !(app.platform & dev)) return false; 
                return true;
            });

            if (unique) {
                const seen = {};
                filtered.forEach(app => {
                    const key = app.bundle_id || app.title;
                    if (!seen[key] || app.min_os > seen[key].min_os) {
                        seen[key] = app;
                    }
                });
                filtered = Object.values(seen);
            }
            return filtered;
        }

        function applyFilters(page = 0) {
            if (!appsLoaded) return;
            currentPage = page;
            currentFiltered = getFilteredApps();
            
            const countDiv = document.getElementById('searchResultCount');
            if (countDiv) {
                if (currentFiltered.length > 0) {
                    const startRange = (currentPage * PER_PAGE) + 1;
                    const endRange = Math.min((currentPage + 1) * PER_PAGE, currentFiltered.length);
                    countDiv.textContent = `Showing ${startRange.toLocaleString()}-${endRange.toLocaleString()} of ${currentFiltered.length.toLocaleString()} results`;
                    countDiv.style.display = 'block';
                } else {
                    countDiv.style.display = 'none';
                }
            }

            searchResults.classList.remove('results-fade-in');
            void searchResults.offsetWidth; // Force reflow
            searchResults.classList.add('results-fade-in');

            renderGrid(currentFiltered, currentPage);
            renderPagination(currentFiltered.length, currentPage);
            saveConfig();
        }

        function renderPagination(total, page) {
            const nav = document.getElementById('pagination');
            const totalPages = Math.ceil(total / PER_PAGE);
            nav.innerHTML = '';
            
            if (totalPages <= 1) return;

            const wrap = document.createElement('div');
            wrap.className = 'pagination-wrap';

            const range = 2;
            let start = Math.max(0, page - range);
            let end = Math.min(totalPages - 1, page + range);

            const smoothScroll = () => window.scrollTo({ top: 0, behavior: 'smooth' });

            if (page > 0) {
                const prev = document.createElement('button');
                prev.innerHTML = '&laquo;';
                prev.onclick = () => { applyFilters(page - 1); smoothScroll(); };
                wrap.appendChild(prev);
            }

            for (let i = start; i <= end; i++) {
                const btn = document.createElement('button');
                btn.textContent = i + 1;
                if (i === page) btn.className = 'active';
                btn.onclick = () => { applyFilters(i); smoothScroll(); };
                wrap.appendChild(btn);
            }

            if (page < totalPages - 1) {
                const next = document.createElement('button');
                next.innerHTML = '&raquo;';
                next.onclick = () => { applyFilters(page + 1); smoothScroll(); };
                wrap.appendChild(next);
            }

            nav.appendChild(wrap);
        }

        function randomIPA() {
            if (!appsLoaded) return;

            // Clear keywords and reset page to allow true random discovery
            searchInput.value = '';
            bundleInput.value = '';
            currentPage = 0; 
            saveConfig(); 

            const filteredPool = getFilteredApps();
            if (filteredPool.length === 0) {
                alert('No apps match your current filters. Try changing the OS or Device settings.');
                return;
            }
            const idx = Math.floor(Math.random() * filteredPool.length);
            const app = filteredPool[idx];
            
            searchResults.innerHTML = '';
            emptyState.style.display = 'none';
            document.getElementById('pagination').innerHTML = '';

            const container = document.createElement('div');
            container.className = 'hero-random-container';
            
            const card = document.createElement('div');
            card.className = 'app-card-grid-aesthetic hero-card';
            card.innerHTML = `
                <div class="card-icon-glossy" style="width:120px; height:120px; border-radius:24px;">
                    ${app.icon ? `<img src="${app.icon}" alt="${escapeHtml(app.title)}" loading="lazy" onerror="this.onerror=null;this.parentElement.innerHTML='<i class=\\'fas fa-mobile-alt\\' style=\\'font-size:40px;\\'></i>'">` : '<i class="fas fa-mobile-alt" style="font-size:40px;"></i>'}
                </div>
                <div class="card-name-glossy" style="font-size:20px;">${renderAppTitle(app)}</div>
                <div class="card-meta-glossy" style="font-size:14px; margin:10px 0;">
                    <span class="meta-v">v${escapeHtml(app.version)}</span>
                    <span class="meta-s">${(app.fsize / 1024).toFixed(1)} MB</span>
                </div>
                <div class="card-os-glossy" style="font-size:13px; margin-bottom:20px;">
                    iOS ${formatOS(app.min_os)}+
                    ${getPlatformIcons(app.platform)}
                </div>
                <button class="ios-btn-blue" style="width:100%; border-radius:20px;" onclick="openModal('${app.pk}')">View App</button>
            `;
            container.appendChild(card);
            searchResults.appendChild(container);
        }

        function renderGrid(data, page = 0) {
            searchResults.innerHTML = '';
            const slice = data.slice(page * PER_PAGE, (page + 1) * PER_PAGE);
            
            if (slice.length === 0) {
                emptyState.style.display = 'flex';
                return;
            }
            emptyState.style.display = 'none';

            slice.forEach(app => {
                const card = document.createElement('div');
                card.className = 'app-card-grid-aesthetic';
                card.innerHTML = `
                    <div class="card-icon-glossy">
                        ${app.icon ? `<img src="${app.icon}" alt="${escapeHtml(app.title)}" loading="lazy" onerror="this.onerror=null;this.parentElement.innerHTML='<i class=\\'fas fa-mobile-alt\\'></i>'">` : '<i class="fas fa-mobile-alt"></i>'}
                    </div>
                    <div class="card-name-glossy">${renderAppTitle(app)}</div>
                    <div class="card-meta-glossy">
                        <span class="meta-v">v${escapeHtml(app.version)}</span>
                        <span class="meta-s">${(app.fsize / 1024).toFixed(1)} MB</span>
                    </div>
                    <div class="card-os-glossy">iOS ${formatOS(app.min_os)}+ ${getPlatformIcons(app.platform)}</div>
                    <button class="get-btn-glossy" onclick="openModal('${app.pk}')">Get</button>
                `;
                searchResults.appendChild(card);
            });
        }

        const VERSIONS_PER_PAGE = 21;

        function renderVersionPage(bundleId, page = 0) {
            const container = document.querySelector(`.versions-container[data-bid="${bundleId}"]`);
            const pagination = document.querySelector(`.versions-pagination[data-bid="${bundleId}"]`);
            if (!container || !pagination) return;

            // Sort versions ASCENDING (Oldest first) to match the main database logic
            const allVersions = apps.filter(a => a.bundle_id === bundleId && a.bundle_id !== '').sort((a,b) => {
                if (a.min_os !== b.min_os) return a.min_os - b.min_os;
                return String(a.version).localeCompare(b.version, undefined, {numeric: true});
            });
            const list = allVersions.length > 0 ? allVersions : []; 
            
            const totalPages = Math.ceil(list.length / VERSIONS_PER_PAGE);
            const slice = list.slice(page * VERSIONS_PER_PAGE, (page + 1) * VERSIONS_PER_PAGE);

            container.innerHTML = `<ul class="version-list">` + slice.map(v => {
                const url = getAppUrl(v);
                const filename = v.path.split('/').pop();
                return `
                <li class="version-li">
                    <div class="version-header-row">
                        <div class="version-icon-mini card-icon-glossy">
                            ${v.icon ? `<img src="${v.icon}" alt="v${v.version}" loading="lazy" onerror="this.onerror=null;this.parentElement.innerHTML='<i class=\\'fas fa-mobile-alt\\'></i>'">` : '<i class="fas fa-mobile-alt"></i>'}
                        </div>
                        <div class="version-info-main">
                            <div class="version-name"><strong>v${v.version}</strong> (${(v.fsize / 1024).toFixed(1)} MB)</div>
                            <div class="version-filename"><a href="${url}" rel="noopener noreferrer nofollow">${filename}</a></div>
                            <div class="version-os">Requires iOS ${formatOS(v.min_os)}+</div>
                        </div>
                    </div>
                    <div class="version-actions-grid" style="grid-template-columns: 1fr 1fr;">
                        <a href="${url}" download class="v-btn-action download-v">Download</a>
                        <button onclick="installIPA('${v.pk}')" class="v-btn-action install-v">Install</button>
                    </div>
                </li>`;
            }).join('') + `</ul>`;

            // Pagination UI
            pagination.innerHTML = '';
            if (totalPages > 1) {
                const wrap = document.createElement('div');
                wrap.className = 'pagination-wrap';
                
                const range = 2;
                let start = Math.max(0, page - range);
                let end = Math.min(totalPages - 1, page + range);

                // First
                if (page > 0) {
                    const first = document.createElement('button');
                    first.innerHTML = '&laquo;&laquo;';
                    first.title = 'First Page';
                    first.onclick = (e) => { e.stopPropagation(); renderVersionPage(bundleId, 0); };
                    wrap.appendChild(first);
                }

                if (page > 0) {
                    const prev = document.createElement('button');
                    prev.innerHTML = '&laquo;';
                    prev.onclick = (e) => { e.stopPropagation(); renderVersionPage(bundleId, page - 1); };
                    wrap.appendChild(prev);
                }

                for (let i = start; i <= end; i++) {
                    const btn = document.createElement('button');
                    btn.textContent = i + 1;
                    if (i === page) btn.className = 'active';
                    btn.onclick = (e) => { e.stopPropagation(); renderVersionPage(bundleId, i); };
                    wrap.appendChild(btn);
                }

                if (page < totalPages - 1) {
                    const next = document.createElement('button');
                    next.innerHTML = '&raquo;';
                    next.onclick = (e) => { e.stopPropagation(); renderVersionPage(bundleId, page + 1); };
                    wrap.appendChild(next);
                }

                // Last
                if (page < totalPages - 1) {
                    const last = document.createElement('button');
                    last.innerHTML = '&raquo;&raquo;';
                    last.title = 'Last Page';
                    last.onclick = (e) => { e.stopPropagation(); renderVersionPage(bundleId, totalPages - 1); };
                    wrap.appendChild(last);
                }

                // Jump Spot (...) - Only show if there are many pages (e.g. > 10)
                if (totalPages > 10) {
                    const spot = document.createElement('input');
                    spot.type = 'number';
                    spot.placeholder = '...';
                    spot.className = 'pagination-spot';
                    spot.onkeydown = (e) => {
                        if (e.key === 'Enter') {
                            let p = parseInt(spot.value) - 1;
                            if (!isNaN(p) && p >= 0 && p < totalPages) {
                                renderVersionPage(bundleId, p);
                            } else {
                                spot.value = '';
                            }
                        }
                    };
                    wrap.appendChild(spot);
                }

                pagination.appendChild(wrap);
            }
        }

        // ── Segmented Control Logic ──────────────────────────────────────────
        document.querySelectorAll('.segment').forEach(seg => {
            seg.addEventListener('click', function() {
                const parent = this.parentElement;
                parent.querySelectorAll('.segment').forEach(s => s.classList.remove('active'));
                this.classList.add('active');
                
                const hiddenInput = document.getElementById('device');
                hiddenInput.value = this.dataset.value;
                applyFilters();
            });
        });

        function setActiveSegment(value) {
            const segments = document.querySelectorAll('.segment');
            segments.forEach(s => {
                if (s.dataset.value == (value || '')) {
                    s.classList.add('active');
                } else {
                    s.classList.remove('active');
                }
            });
        }

        // ── Modal ────────────────────────────────────────────────────────────
        function createModal(app) {
            const modal = document.createElement('div');
            modal.className = 'modal-overlay';
            modal.innerHTML = `
                <div class="modal-sub-header">
                    <button class="modal-close-btn" onclick="closeModal(this)">Close</button>
                    <div class="modal-sub-header-title">App Info</div>
                    <div style="width:70px;"></div>
                </div>
                <div class="modal-content">
                    <div class="modal-app-header">
                        <div class="modal-app-icon">${app.icon ? `<img src="${app.icon}" alt="${escapeHtml(app.title)}" loading="lazy">` : '<i class="fas fa-mobile-alt"></i>'}</div>
                        <div class="modal-app-info">
                            <div class="modal-app-title">${escapeHtml(app.title)}</div>
                            <div class="modal-app-developer">${escapeHtml(app.bundle_id)}</div>
                        </div>
                        <button class="random-btn-header" style="width: 80px; height: 32px;" onclick="toggleVersions(this)">Get</button>
                    </div>
                    
                    <div class="modal-section">
                        <h3><i class="fas fa-mobile-alt"></i> Compatibility</h3>
                        <p>Requires iOS ${formatOS(app.min_os)} or later.</p>
                        <p>Devices: ${getPlatformIcons(app.platform)}</p>
                    </div>

                    <div class="modal-section">
                        <h3><i class="fas fa-screwdriver-wrench"></i> Install on device</h3>
                        <p style="font-size:12px; color:#666; margin-bottom:10px;">
                            Unfortunatelly, this function is not possible with static HTML+JS. 
                            You must provided a plist generator URL. See <a href="https://github.com/stuffed18/ipa-archive-updated?tab=readme-ov-file#starting-plist-server" target="_blank" style="color:#0a84ff; text-decoration:underline;">Readme</a> file for further 
                            instructions on how to set up such a service.
                        </p>
                        <button onclick="document.getElementById('plistConfigArea').style.display='flex'" class="get-btn-glossy" style="padding:6px 12px; font-size:12px; margin-bottom:10px;">Configure now</button>
                        <div id="plistConfigArea" class="plist-config-row" style="display: ${plistServerUrl ? 'flex' : 'none'}; flex-wrap: wrap;">
                            <input type="text" id="plistServerInput" value="${plistServerUrl}" placeholder="http://192.168.0.1/" class="ios-input" style="font-size:12px; padding:6px 10px; flex: 1; min-width: 180px;">
                            <button onclick="savePlistServer()" class="get-btn-glossy" style="padding:6px 12px; font-size:12px; background: var(--ios-blue); color: white; border-color: #135a9a;">Save</button>
                            <button onclick="document.getElementById('plistConfigArea').style.display='none'" class="get-btn-glossy" style="padding:6px 12px; font-size:12px; background: var(--ios-grey); color: #333; border-color: #bbb;">Abort</button>
                        </div>
                    </div>

                    <div class="version-sheet-overlay" onclick="if(event.target === this) this.classList.remove('active')">
                        <div class="version-sheet">
                            <h3 class="version-sheet-title">Version History</h3>
                            <div class="versions-container" data-bid="${app.bundle_id}"></div>
                            <div class="versions-pagination ios-pagination" style="margin-top:15px;" data-bid="${app.bundle_id}"></div>
                        </div>
                    </div>
                </div>`;
            
            setTimeout(() => renderVersionPage(app.bundle_id, 0), 0);
            return modal;
        }

        function savePlistServer() {
            const val = document.getElementById('plistServerInput').value.trim();
            if (val && !val.startsWith('http')) { alert('URL must start with http:// or https://'); return; }
            plistServerUrl = val;
            localStorage.setItem('plistServerUrl', val);
            alert('Settings saved!');
        }

        function installIPA(pk) {
            if (!plistServerUrl) {
                alert('Please configure a Plist Server URL in the "Installation" section.');
                return;
            }
            const app = apps.find(a => a.pk == pk);
            if (!app) return;

            const thisServerUrl = location.href.split('#')[0].split('?')[0];
            const data = {
                u: getAppUrl(app),
                n: app.title,
                b: app.bundle_id,
                v: app.version.split(' ')[0],
                i: thisServerUrl + app.icon
            };
            
            const b64 = btoa(unescape(encodeURIComponent(JSON.stringify(data)))).replace(/=/g, '');
            const plistUrl = plistServerUrl + (plistServerUrl.includes('?') ? '&' : '?') + 'd=' + b64;
            window.open('itms-services://?action=download-manifest&url=' + encodeURIComponent(plistUrl));
        }

        function openModal(pk) {
            const app = apps.find(a => a.pk == pk);
            if (!app) return;
            const modal = createModal(app);
            modalContainer.appendChild(modal);
            document.body.style.overflow = 'hidden';

            setTimeout(() => {
                modal.classList.add('active');
                renderVersionPage(app.bundle_id, 0); // Always open to Page 1
            }, 10);
        }

        function closeModal(btn) {
            const modal = btn.closest('.modal-overlay');
            modal.classList.remove('active');
            document.body.style.overflow = 'auto';
            setTimeout(() => modal.remove(), 400);
        }

        function toggleVersions(btn) {
            const sheet = btn.closest('.modal-content').querySelector('.version-sheet-overlay');
            sheet.classList.toggle('active');
        }

        // ── Config ───────────────────────────────────────────────────────────
        function saveConfig() {
            const data = {
                q: searchInput.value,
                bid: bundleInput.value,
                min: minosInput.value,
                max: maxosInput.value,
                dev: deviceSelect.value,
                uni: uniqueCheck.checked,
                p: currentPage
            };
            const params = new URLSearchParams(data);
            window.history.replaceState({}, '', window.location.pathname + '#' + params.toString());
        }

        function loadConfig() {
            if (!location.hash) return;
            const params = new URLSearchParams(location.hash.substring(1));
            searchInput.value = params.get('q') || '';
            bundleInput.value = params.get('bid') || '';
            minosInput.value = params.get('min') || '';
            maxosInput.value = params.get('max') || '';
            const devValue = params.get('dev') || '';
            deviceSelect.value = devValue;
            setActiveSegment(devValue);
            uniqueCheck.checked = params.get('uni') !== 'false';
            currentPage = parseInt(params.get('p')) || 0;
            if (location.hash.length > 2) applyFilters(currentPage);
        }

        appsPromise.then(() => {
            loadConfig();
        }).catch(e => {
            console.error(e);
        });
