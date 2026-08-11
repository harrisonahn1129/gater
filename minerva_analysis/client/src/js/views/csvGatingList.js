/**
 * @class CSVGatingList - A view to select and deselect channels for gating, and to set gates (filter ranges) in the tabular data
 */
class CSVGatingList {

    /**
     * @constructor
     * @param config the cinfiguration file (json)
     * @param columns - all the channel names
     * @param dataLayer - the data layer (stub) that executes server requests and holds client side data
     * @param eventHandler - the event handler for distributing interface and data updates
     */
    constructor(config, columns, dataLayer, eventHandler) {
        this.config = config;
        this.columns = [...columns];
        this.databaseDescription = {};
        this.maxSelections = config.maxSelections;
        this.eventHandler = eventHandler;
        this.dataLayer = dataLayer;
        this.selections = {};
        this.hasGatingGMM = {};
        this.gatingIDs = {};
        this.sliders = new Map();
        this.container = d3.select("#csv_gating_list");
        // Gating vars
        const { channelList } = __minervaAnalysis;
        this.global_channel_list = channelList;
        this.global_image_channels = imageChannels;
        this.gating_default_range = [0, 65536];
        this.gating_channels = this.initGatingChannels();
        this.gating_list = null;
        // Download vars
        this.download_panel_visible = false;
        this.download_input1 = null;
        this.download_input2 = null;
        // Eval settings
        this.eval_mode = 'and'
    }

     /**
     * Selects a channel as active and adds the respective visual components to the channel panel in the list view
     * @param name - the channel to set and display as selected
     */
    selectChannel(name) {
        const fullName = this.dataLayer.getFullChannelName(name);
        const values = this.gating_channels[fullName];
        this.selections[fullName] = values;
        this.sliders.get(name).value(values);
        this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_MOVE, this.selections);
        if (!(name in this.hasGatingGMM)) {
            this.getAndDrawGatingGMM(name).then(() => {
                this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_END, this.selections);
            });
        }
    }

     /**
     * Removes a channel form the current selection
     * @param name - the name of the channel to remove
     */
    removeChannel(name) {
        // Delete
        const fullName = this.dataLayer.getFullChannelName(name);
        delete this.selections[fullName];

        // Trigger
        this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_END, this.selections);
    }

     /**
     * initializes the view (channel list)
     * @param dd - database description
     * @param seaDragonViewer - the ImageViewer instance
     * @returns {Promise<void>}
     */
    init(dd, seaDragonViewer) {
        this.databaseDescription = dd;
        this.seaDragonViewer = seaDragonViewer; 
        document.getElementById('drag-and-drop-info').style.display = "none";
        // Hide the Loader
        document.getElementById('csv_gating_list_loader').style.display = "none";
        this.gating_list = document.getElementById("csv_gating_list");
        let list = document.createElement("ul");
        list.classList.add("list-group")
        list.setAttribute("id", "gating_list_ul")
        this.gating_list.appendChild(list)
        const gatingListEl = document.getElementById("csv_gating_list");
        const swidth = gatingListEl.getBoundingClientRect().width;
        // Will show the picker when you click on a color rect
        let showPicker = () => {
            this.colorTransfrHandle = d3.select(d3.event.target);
            let color = this.colorTransferHandle.style('fill');
            let hsl = d3.hsl(color);
            this.rainbow.show(d3.event.clientX, d3.event.clientY);
        };
        // Draws rows in the gating list
        this.columns.push('Area'); // Add 'Area' to Gating List
        _.each(this.columns, (column, index) => {
            let channelID = `channel_${index}`;
            this.gatingIDs[column] = channelID;
            // div for each row in gating list
            let listItemParentDiv = document.createElement("div");
            listItemParentDiv.classList.add("list-group-item");
            listItemParentDiv.classList.add("container");
            listItemParentDiv.classList.add("gating-list-content");
            // row
            let row = document.createElement("div");
            row.classList.add("row");
            listItemParentDiv.appendChild(row);
            // row
            let row2 = document.createElement("div");
            row2.classList.add("row");
            listItemParentDiv.appendChild(row2);

            // column within row that contains the name of the gating
            let nameCol = document.createElement("div");
            nameCol.classList.add("col-md-4");
            nameCol.classList.add("gating-col");
            row.appendChild(nameCol);

            // column within row that cintains the slider for the gating
            let sliderCol = document.createElement("div");
            sliderCol.classList.add("col-md-12");
            sliderCol.classList.add("csv_gating-slider");
            sliderCol.setAttribute('id', "csv_gating-slider_" + channelID)
            row2.appendChild(sliderCol);

            // column within row that contains svg for color pickers
            let svgCol = document.createElement("div");
            svgCol.classList.add("col-md-4");
            svgCol.classList.add("ml-auto");
            svgCol.classList.add("gating-col");
            svgCol.classList.add("gating-svg-wrapper");
            svgCol.classList.add("col-svg-wrapper");
            row.appendChild(svgCol);


            let svg = d3.select(svgCol)
                .append("svg")
                .attr("width", 30)
                .attr("height", 15)
            svgCol.style.display = "none";

            let gatingName = document.createElement("span");
            gatingName.classList.add('gating-name');
            gatingName.classList.add('list-button');
            gatingName.textContent = column;
            nameCol.appendChild(gatingName);
            listItemParentDiv.addEventListener("click", e => {
                return this.toggleChannelPanel(e, svgCol);
            })
            list.appendChild(listItemParentDiv);

            //add and hide gating sliders (will be visible when gating is active)
            const fullName = this.dataLayer.getFullChannelName(column);
            const sliderRange = [this.databaseDescription[fullName].min, this.databaseDescription[fullName].max];
            this.gating_channels[fullName] = sliderRange;
            const gatingListEl = document.getElementById("csv_gating_list");
            const swidth = gatingListEl.getBoundingClientRect().width;
            this.addSlider(column, swidth, sliderRange, sliderRange);
            d3.select('div#csv_gating-slider_' + channelID).style('display', "none");

            let autoCol = document.createElement("div");
            autoCol.classList.add("col-md-4");
            autoCol.classList.add("ml-auto");
            autoCol.classList.add("csv_gating-auto")
            autoCol.setAttribute('id', "csv_gating-auto_" + channelID)
            autoCol.classList.add("gating-col");
            autoCol.classList.add("gating-svg-wrapper");
            row.appendChild(autoCol);

            let autoBtn = document.createElement("button");
            autoBtn.classList.add('auto-btn');
            autoBtn.classList.add('auto-loading');
            autoBtn.setAttribute('id', "auto-btn-gating_" + channelID);
            autoBtn.textContent = "auto";
            autoBtn.addEventListener("click", async () => {
                const shortName = this.dataLayer.getShortChannelName(fullName);
                await this.autoGate(shortName);
            });

            autoCol.appendChild(autoBtn);
            autoBtn.addEventListener("click", e => e.stopPropagation());
            d3.select(autoCol).style('display', "none");
        });

        var dropzone = new Dropzone("#csv_gating_list", {
            url: "/upload_gates",
            clickable: false,
            disablePreview: true,
            createImageThumbnails: false
        });
        dropzone.on("sending", (file, xhr, formData) => {
            formData.append("datasource", datasource);
        });
        dropzone.on("queuecomplete", (file, xhr, formData) => {
            return this.applyGates()
        });

        // Load a gating CSV that lives on the OMERO image (was: a local file
        // picker uploading to the server's disk). Any CSV attached to the image
        // is offered, not just the ones Gater wrote.
        let arrow = document.getElementById('gating_upload_icon')
        arrow.onclick = async () => {
            await this.loadGatingCsvFromOmero();
        }

        let arrow_db = document.getElementById('gating_upload_icon_db')
        arrow_db.onclick = async () => {
            await this.applyGates('db')
        }


        // Adding dropzone for CSV_Gating_List
        let parent = document.getElementById('csv_gating_list');
        let rect = parent.getBoundingClientRect();
        parent.addEventListener("dragover", (ev) => {
            document.getElementById('gating_list_ul').style.display = "none";
            document.getElementById('drag-and-drop-info').style.display = "block";
        })
        parent.addEventListener("dragleave", (ev) => {
            if (ev.x > rect.left + rect.width || ev.x < rect.left
                || ev.y > rect.top + rect.height || ev.y < rect.top) {
                document.getElementById('gating_list_ul').style.display = "block";
                document.getElementById('drag-and-drop-info').style.display = "none";
            }
        })
        parent.addEventListener("drop", (ev) => {
            document.getElementById('gating_list_ul').style.display = "block";
            document.getElementById('drag-and-drop-info').style.display = "none";
        })

        // Add events
        this.addDownloadEvents();
        this.addEventsLinked();
    }

     /**
     * @function applyGates
     * Applies gating settings to the gates in the tool.
     * @param {String} source 'csv' to apply the rows passed in, 'file' for a
     *                        drag-and-dropped upload, anything else to fetch
     *                        this datasource's saved state
     * @param {Array}  providedGates rows to apply when source === 'csv'
     * @returns {Number} how many rows matched a channel in this image (Lasso
     *                   rows count too). A CSV from another image can parse
     *                   fine yet match nothing, so the caller must be able to
     *                   say so rather than appearing to succeed silently.
     */
    async applyGates(source, providedGates) {
        let gates;
        if (source === 'csv') {
            gates = providedGates || [];
        } else if (source === 'file'){
            gates = await this.dataLayer.getUploadedGatingCsvValues();
        } else {
            gates = await this.dataLayer.getSavedGatingList();
        }
        let applied = 0;

        this.eventHandler.trigger(CSVGatingList.events.RESET_GATINGLIST)
         let list_uploaded_lassos = [];
        _.each(gates, async (col) => {
            if (col.channel == 'Lasso') {
                list_uploaded_lassos.push(col);
                applied++;
            } else {
                let shortName = this.dataLayer.getShortChannelName(col.channel);
                let channelID = this.gatingIDs[shortName];
                if (this.sliders.get(shortName)) {
                    applied++;
                    let toggle_off
                    if (!col.gate_active && col.channel in this.selections) {
                        toggle_off = true;
                    } else {
                        toggle_off = false;
                    }
                    this.gating_channels[col.channel] = [col.gate_start, col.gate_end];
                    if (col.gate_active) {
                        // IF the channel isn't active, make it so
                        if (!this.selections[col.channel]) {
                            let selector = `#csv_gating-slider_${channelID}`;
                            document.querySelector(selector).click();
                        }
                        this.selections[col.channel] = [col.gate_start, col.gate_end];
                        
                        // Update the slider values to reflect the new gate
                        const slider = this.sliders.get(shortName);
                        if (slider) {
                            // Apply the same data type conversion as in addSlider
                            const transformed = this.dataLayer.isTransformed();
                            const v0 = transformed ? col.gate_start : Math.round(col.gate_start);
                            const v1 = transformed ? col.gate_end : Math.round(col.gate_end);
                            
                            slider.silentValue([v0, v1]);
                            // Update the input fields
                            d3.select('#gating_slider-input_' + channelID + '_0').attr('value', v0);
                            d3.select('#gating_slider-input_' + channelID + '_0').property('value', v0);
                            d3.select('#gating_slider-input_' + channelID + '_1').attr('value', v1);
                            d3.select('#gating_slider-input_' + channelID + '_1').property('value', v1);
                        }

                    } else {
                        // If channel is currently active, but shouldn't be, update it
                        if (toggle_off) {
                            let selector = `#csv_gating-slider_${channelID}`;
                            document.querySelector(selector).click();
                        }
                        delete this.selections[col.channel];
                    }
                }
            }
        })
        // Trigger brush
        this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_END, this.selections);

        await this.seaDragonViewer.clear_lassos();
        // Lassos that came from a CSV carry their polygon as text (the saved
        // state path already has it as an object). A malformed one is skipped
        // rather than aborting the whole apply -- CSVs can come from anywhere.
        if (source === 'file' || source === 'csv'){
            list_uploaded_lassos = list_uploaded_lassos.reduce((acc, item) => {
                try {
                    if (typeof item['gate_start'] === 'string') {
                        item['gate_start'] = JSON.parse(item['gate_start'].replace(/'/g, '"'));
                    }
                    acc.push(item);
                } catch (e) {
                    console.log("Skipping unreadable lasso in CSV", item, e);
                    applied--;
                }
                return acc;
            }, []);
        }
        for (let lasso of list_uploaded_lassos){
            await this.seaDragonViewer.upload_lasso(lasso);
        }
        return applied;
    }


    /**
     * @function loadGatingCsvFromOmero
     * Lists the CSV attachments on this datasource's OMERO image, lets the user
     * pick one, then applies it to the gating list.
     */
    async loadGatingCsvFromOmero() {
        let csvs;
        try {
            // Only gate-range CSVs Gater saved. This also excludes the per-cell
            // encoding exports, which live in their own namespace and cannot be
            // loaded back as gates.
            csvs = await this.dataLayer.listOmeroGatingCsvs('gating_csv', true);
        } catch (e) {
            alert(e.message);
            return;
        }
        if (!csvs.length) {
            alert("No gating CSVs have been saved to this image yet.\n\n" +
                "Use \"Save gated channel ranges to OMERO\" in the save panel " +
                "to create one.");
            return;
        }

        const chosen = await GaterCsvDialogs.pickOmeroCsv(csvs, {
            title: 'Load gating from a CSV on OMERO',
            subtitle: 'Gating CSVs saved to this image.'
        });
        if (!chosen) return;                       // cancelled

        let rows;
        try {
            rows = await this.dataLayer.getOmeroGatingCsvValues(chosen.id);
        } catch (e) {
            alert(e.message);
            return;
        }

        const applied = await this.applyGates('csv', rows);
        if (!applied) {
            // Parsed fine but nothing matched -- almost always a CSV whose
            // channel names come from a different image.
            alert(`Loaded "${chosen.name}", but none of its ${rows.length} ` +
                `row(s) match this image's channels. Nothing changed.`);
        }
    }


    /**
     * @function saveGatingCsvToOmero
     * Writes one of the download panel's two CSVs to the OMERO image, using the
     * name in the panel's own input. Warns before replacing an existing file.
     * @param {Boolean} fullCsv false = gate ranges, true = per-cell encodings
     * @param {HTMLElement} nameInput the panel input holding the file name
     */
    async saveGatingCsvToOmero(fullCsv, nameInput) {
        // Re-entry guard. The busy overlay already blocks the pointer, but a
        // keyboard-activated button or a second panel could still get through,
        // and one queued export is enough to tie up a server thread for a
        // minute.
        if (this._csvSaveInFlight) return;
        this._csvSaveInFlight = true;
        try {
            await this._saveGatingCsvToOmero(fullCsv, nameInput);
        } finally {
            this._csvSaveInFlight = false;
        }
    }


    async _saveGatingCsvToOmero(fullCsv, nameInput) {
        // Show the canonical name in the input so what the user sees is what
        // OMERO will store (spaces become '_').
        const canonical = GaterCsvDialogs.canonical(
            nameInput.value || this.dataLayer.defaultGatingCsvName(fullCsv));
        nameInput.value = canonical;

        const kind = fullCsv ? 'gating_cells_csv' : 'gating_csv';
        let overwrite = false;
        try {
            const existing = await this.dataLayer.listOmeroGatingCsvs(kind);
            const taken = existing.some(c => c.is_gater &&
                GaterCsvDialogs.canonical(c.description || c.name) === canonical);
            if (taken) {
                const go = await GaterCsvDialogs.confirmDialog(
                    'A CSV with this name already exists',
                    `"${canonical}" already exists on this image. Saving will ` +
                    `replace it — cancel to change the name first.`,
                    'Overwrite');
                if (!go) return;
                overwrite = true;
            }
        } catch (e) {
            // Listing failed; let the save proceed -- the server re-checks and
            // returns 409, handled below.
            console.log("Could not list existing CSVs on OMERO", e);
        }

        // The per-cell export walks the whole quantification table, so it can
        // take a minute. Block the UI while it runs: without that the app looks
        // frozen, and the retry clicks that invites are what queued up several
        // saves and eventually crashed the server.
        const busy = GaterCsvDialogs.showSaveBusy(
            `Saving "${canonical}" to OMERO`,
            fullCsv ? 'Per-cell exports cover every cell in the quantification '
                      + 'table and can take a minute. Please wait.'
                    : 'Please wait.');

        const save = (force) => this.dataLayer.saveGatingCsvToOmero(
            this.gating_channels, this.selections,
            this.seaDragonViewer.list_lassos,
            fullCsv ? this.seaDragonViewer.pickedIds : null,
            fullCsv, canonical, force);

        try {
            const res = await save(overwrite);
            busy.close();
            alert(`Saved "${res.name || canonical}" to OMERO`);
        } catch (e) {
            busy.close();
            if (e.exists) {
                // Created between listing and saving.
                const go = await GaterCsvDialogs.confirmDialog(
                    'A CSV with this name already exists', e.message, 'Overwrite');
                if (go) {
                    const busy2 = GaterCsvDialogs.showSaveBusy(
                        `Replacing "${canonical}" on OMERO`, 'Please wait.');
                    try {
                        const res = await save(true);
                        alert(`Saved "${res.name || canonical}" to OMERO`);
                    } catch (e2) {
                        alert(e2.message);
                    } finally {
                        busy2.close();
                    }
                }
                return;
            }
            alert(e.message);
        }
    }

    /**
     * @function autoGate - applies thresholds based on Gaussian Mixture Model
     * @param name - the name of the channel to apply it to
     */
    async autoGate(shortName) {
        const fullName = this.dataLayer.getFullChannelName(shortName);
        const input = this.hasGatingGMM[shortName]['gate'].toFixed(7);
        const transformed = this.dataLayer.isTransformed();
        const gate = transformed ? parseFloat(input) : parseInt(input);
        if (fullName in this.selections) {
            const channelID = this.gatingIDs[shortName];
            const gate_end = this.selections[fullName][1];
            const slider = this.sliders.get(shortName);
            const values = [gate, gate_end];
            this.moveSliderHandles(slider, values, shortName, 'GATING_BRUSH_END');
            d3.select('#gating_slider-input_' + channelID + '_0').attr('value', gate)
            d3.select('#gating_slider-input_' + channelID + '_0').property('value', gate);
        }
    }

    /**
     * @function initGatingChannels - creates the data structure for channels
     * @return obj - allChannels and their default range
     */
    initGatingChannels() {

        // Init
        const obj = {};

        // Iterate to create fields
        for (let key in this.global_image_channels) {

            obj[key] = this.gating_default_range;
        }

        // Return
        return obj;
    }

    /**
     * @function toggleChannelPane - expands or collapses a channel panel in the list that was clicked on
     * @param event - the click vent
     * @param svgCol - the column to expand or collapse
     */
    toggleChannelPanel(event, svgCol) {

        // If you clicked on the svg, ignore this behavior
        if (event.target.closest("svg")) {
            return;
        }

        // Get info
        let parent = event.target.closest(".list-group-item");
        let name = parent.querySelector('.gating-name').textContent;
        let channelID = this.gatingIDs[name];
        let status = !parent.classList.contains("active");

        // If active - else inactive
        if (status) {

            // Clear everything
            // clearOut();

            // Don't add gating is the max are selected
            if (_.size(this.selections) >= this.maxSelections) {
                return;
            }

            // Update properties and add slider
            d3.select(parent).classed("active", true);
            svgCol.style.display = "block";
            d3.select('div#csv_gating-slider_' + channelID).style('display', "block")
            d3.select('div#csv_gating-auto_' + channelID).style('display', "block");

            // Add channel
            this.selectChannel(name);

        } else {
            // Clear panel visibility
            // clearOut();

            // Remove channel and rerender
            this.removeChannel(name);

            // Hide
            d3.select(parent).classed("active", false);
            svgCol.style.display = "none";
            d3.select('div#csv_gating-slider_' + channelID).style('display', "none")
            d3.select('div#csv_gating-auto_' + channelID).style('display', "none");
        }

        let selectionsHeaderDiv = document.getElementById("csv_selected-gatings-header-div");
        if (selectionsHeaderDiv) {
            if (_.size(this.selections) >= this.maxSelections) {
                selectionsHeaderDiv.classList.add('bold-selections-header');
            } else {
                selectionsHeaderDiv.classList.remove('bold-selections-header');
            }
            document.getElementById("csv_num-selected-gatings").textContent = _.size(this.selections);

            // Trigger event
            const packet = {selections: this.selections, name, status};
            this.eventHandler.trigger(CSVGatingList.events.GATING_CHANNELS_CHANGE, packet);

        }
    }

    /**
     * @function addDownloadEvents - adds eventl listeners an functionality to the download buttons
     */
    addDownloadEvents() {

        // Els
        const gating_download_icon_db = document.querySelector('#gating_download_icon_db');
        const gating_download_icon = document.querySelector('#gating_download_icon');
        const gating_download_panel = document.querySelector('#gating_download_panel');
        const gating_exit = document.querySelector('#gating_exit');
        const download_gated_channel_ranges = document.querySelector('#download_gated_channel_ranges');
        const download_gated_cell_encodings = document.querySelector('#download_gated_cell_encodings');
        const download_input1 = document.querySelector('#download_input1');
        const download_input2 = document.querySelector('#download_input2');
        const gating_controls_outlines = document.querySelector('#gating_controls_outlines')
        const gating_controls_centroids= document.querySelector('#gating_controls_centroids')

        // Events ::

        gating_download_icon_db.addEventListener('click', () => {
            this.dataLayer.saveGatingList(this.gating_channels, this.selections, this.seaDragonViewer.list_lassos);
            alert("Saved Gating to OMERO");
        })

        // Open / close download panel
        gating_download_icon.addEventListener('click', () => {
            // Update class var
            this.download_panel_visible = !this.download_panel_visible;
            // Condition to update download panel visibility
            if (this.download_panel_visible) {
                gating_download_panel.style.visibility = 'visible';
            } else {
                gating_download_panel.style.visibility = 'hidden';
            }
        });

        // Close download panel
        gating_exit.addEventListener('click', () => {
            // Update class var
            this.download_panel_visible = !this.download_panel_visible;
            // Hide download panel
            gating_download_panel.style.visibility = 'hidden';
        });

        // Keep the CSV name boxes showing exactly what OMERO will store:
        // spaces (and anything else a filename cannot carry) become '_' as the
        // user types, matching the channel CSV naming dialog. The defaults come
        // from the datasource name, which may itself contain spaces, so clean
        // them once up front too.
        [download_input1, download_input2].forEach(el => {
            if (!el) return;
            GaterCsvDialogs.sanitizeInputInPlace(el);
            el.addEventListener('input', () => GaterCsvDialogs.sanitizeInputInPlace(el));
        });

        // Save gated channel ranges to OMERO (was: a browser download). The
        // file name comes from this panel's own input.
        download_gated_channel_ranges.addEventListener('click', async () => {
            await this.saveGatingCsvToOmero(false, download_input1);
        })

        // Save gated cell encodings to OMERO, honouring the encoding select.
        download_gated_cell_encodings.addEventListener('click', async () => {
            await this.saveGatingCsvToOmero(true, download_input2);
        })

        // Toggle outlined / filled cell selections
        gating_controls_outlines.addEventListener('change', e => {
            this.seaDragonViewer.viewerManagerVMain.sel_outlines = e.target.checked;
            this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_END, this.selections);
        })

        // Toggle outlined / filled cell selections
        gating_controls_centroids.addEventListener('change', e => {

            // Update logic mode for selection query
            if (e.target.checked) {
                this.eval_mode = 'or';
            } else {
                this.eval_mode = 'and';
            }

            this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_END, this.selections);
        })

    }

    /**
     * @function addEventsLinked
     * ???
     */
    addEventsLinked() {

        // Add events to channel
        const channelListContent = document.querySelectorAll('.channel-list-content');
        const gatingListContent = document.querySelectorAll('.gating-list-content');

        // Attach
        const attach = (targets, matches, target_class, match_class, global) => {
            targets.forEach(cLC => {
                cLC.addEventListener('click', e => {

                    // If event target is not an svg el (from slider)
                    const svgEls = ['path']
                    if (!svgEls.includes(e.currentTarget.tagName)) {

                        // Get channel name
                        const name = _.get(e.currentTarget.querySelector(`.${target_class}`), 'innerText');

                        // Find match el in gating list
                        if (name) {
                            const match = Array.from(matches).find(
                                gLC => gLC.querySelector(`.${match_class}`).innerText === name);

                            // Emulate click to trigger event in csvGatingList.js
                            if (match && !Array.from(match.classList).includes('active')) {
                                const fakeEvent = {target: match};
                                const svgCol = match.querySelector('.col-svg-wrapper')
                                // global.abstract_click(fakeEvent, svgCol);
                            }
                        }
                    }
                });
            });
        }
        attach(gatingListContent, channelListContent, 'gating-name', 'channel-name',
            this.global_channel_list);

    }

    /**
    * @function addSlider - add a slider
    * @param data - the min and max range of the slider
    * @param activeRange - the predefined values for the lower and upper handle
    * @param name - the name of the slider (used as part of the id)
    * @param swidth - the pixel width of the slider
     */
    addSlider(name, swidth, data, activeRange) {

        if (!data) return;

        const fullName = this.dataLayer.getFullChannelName(name);
        const { xDomain, yDomain, histogramData } = this.histogramData(fullName);
        let channelID = this.gatingIDs[name];

        let data_min
        let data_max
        let handle_min
        let handle_max
        if (this.dataLayer.isTransformed()) {
            data_min = d3.min(data)
            data_max = d3.max(data)
            handle_min = activeRange[0]
            handle_max = activeRange[1]
        } else {
            data_min = parseInt(d3.min(data))
            data_max = parseInt(d3.max(data))
            handle_min = parseInt(activeRange[0])
            handle_max = parseInt(activeRange[1])
        }

        let f = d3.format("d")
        //add range slider row content
        const sliderSimple = d3.sliderBottom()
            .min(data_min)
            .max(data_max)
            .width(swidth - 75)
            .tickFormat(f)
            .fill('orange')
            .ticks(1)
            .default([handle_min, handle_max])
            .handle(
                d3.symbol()
                    .type(d3.symbolCircle)
                    .size(100))
            .tickValues([])
            .on('end', (range) => {
                const transformed = this.dataLayer.isTransformed();
                const v0 = transformed ? range[0] : Math.round(range[0]);
                const v1 = transformed ? range[1] : Math.round(range[1]);
                this.moveSliderHandles(sliderSimple, [v0, v1], name, "GATING_BRUSH_END");
            }).on('onchange', (range) => {
                const transformed = this.dataLayer.isTransformed();
                const v0 = transformed ? range[0] : Math.round(range[0]);
                const v1 = transformed ? range[1] : Math.round(range[1]);
                d3.select('#gating_slider-input_' + channelID + '_0').attr('value', v0)
                d3.select('#gating_slider-input_' + channelID + '_0').property('value', v0);
                d3.select('#gating_slider-input_' + channelID + '_1').attr('value', v1);
                d3.select('#gating_slider-input_' + channelID + '_1').property('value', v1);
                this.moveSliderHandles(sliderSimple, [v0, v1], name, "GATING_BRUSH_MOVE");
            });

        this.sliders.set(name, sliderSimple);

        //create the slider svg and call the slider
        var gSimple = d3
            .select('#csv_gating-slider_' + channelID)
            .append('svg')
            .attr('class', 'svgslider')
            .attr('id', 'csv_gating-slider_svg_' + channelID)
            .attr('width', swidth)
            .attr('height', 80)
            .append('g')
            .attr('transform', 'translate(20,40)');

        let xScale = d3.scaleLinear()
            .domain(xDomain)
            .range([0, swidth - 73])

        let yScale = d3.scaleLinear()
            .domain(yDomain)
            .range([0, 25])

        let line = d3.line()
            .x(d => xScale(d.x))
            .y(d => yScale(d.y))
            .curve(d3.curveMonotoneX)

        const lines = gSimple.selectAll('.distribution_line');
        const paths = lines.data([histogramData]).enter().append('path');
        paths
        .append('path')
        .attr('d', line)
        .attr('class', 'distribution_line')
        .attr('transform', 'translate(0,-31)')
        .attr('fill', 'none')

        gSimple.call(sliderSimple);

        //slider value to be displayed closer to the slider than default
        d3.selectAll('.parameter-value').select('text')
            .attr("y", 10);

        //both handles
        const { sliders } = this;
        const handles = d3.select('#csv_gating-slider_' + channelID).selectAll(".parameter-value");
        handles.each(function (d, i) {
            d3.select(this).append("foreignObject")
            .attr('id', 'c_foreignObject_' + channelID + i)
            .attr("width", 50)
            .attr("height", 40)
            .attr('x', -25)
            .attr( 'y', -17)
            .style('padding',"10px")
            .append("xhtml:body")
            .attr('xmlns','http://www.w3.org/1999/xhtml')
            .style('background', 'none')
            .append('input')
            .attr( 'y', -17)
            .attr('id', 'gating_slider-input_' + channelID + '_' + i)
            .attr('type', 'text')
            .attr('class', 'input')
            .attr('value', () => {
                return sliders.get(name).value()[i]
            });
            //remove the previous text label
            d3.select(this).select('text').remove();
        });

        //entering a value in the input field of a slider handle
        const moveSliderHandles = this.moveSliderHandles.bind(this);
        handles.selectAll('.input').on('keydown', function (event, d) {
            if(event.key == "Enter"){
                const val = parseFloat(this.value.replace("%", ""));
                const vals = sliderSimple.silentValue();
                vals[d.index] = val;
                moveSliderHandles(sliderSimple, vals, name, "GATING_BRUSH_END");
            }
        })

        return sliderSimple;
    };

    histogramData(fullName) {
        const histogramData = this.databaseDescription[fullName].histogram;
        const xMin = Math.min(...histogramData.map(e => e.x));
        const xMax = Math.max(...histogramData.map(e => e.x));
        const yMax = Math.max(...histogramData.map(e => e.y));
        return {
            histogramData,
            xDomain: [xMin, xMax],
            yDomain: [yMax, 0],
        };
    }

    async getGatingGMM(name, selection_ids = []) {
        const fullName = this.dataLayer.getFullChannelName(name);
        let packet = await this.dataLayer.getGatingGMM(fullName, selection_ids);
        this.hasGatingGMM[name] = packet;
        return packet;
    }

    drawGatingGMM(name) {
        let channelID = this.gatingIDs[name];
        const fullName = this.dataLayer.getFullChannelName(name);
        const { xDomain, yDomain } = this.histogramData(fullName);
        const packet = this.hasGatingGMM[name];
        let gmm1Data = packet['gmm_1'];
        let gmm2Data = packet['gmm_2'];

        const gatingListEl = document.getElementById("csv_gating_list");
        const swidth = gatingListEl.getBoundingClientRect().width;

        let xScale = d3.scaleLinear()
            .domain(xDomain)
            .range([0, swidth - 73])

        let yScale = d3.scaleLinear()
            .domain(yDomain)
            .range([0, 25])

        let line = d3.line()
            .x(d => xScale(d.x))
            .y(d => yScale(d.y))
            .curve(d3.curveMonotoneX)

        let gSimple = d3.select('#csv_gating-slider_svg_' + channelID + ' g')

        gSimple.selectAll('.gmm1_line')
            .data([gmm1Data])
            .enter()
            .append('path')
            .attr('d', line)
            .attr('class', 'gmm_line')
            .attr('class', 'gmm_line_'+name)
            .attr('id', 'gmm1_line_'+name)
            .attr('transform', 'translate(0,-31)')
            .attr('fill', 'none')
            .attr('stroke', 'blue')

        gSimple.selectAll('.gmm2_line')
            .data([gmm2Data])
            .enter()
            .append('path')
            .attr('d', line)
            .attr('class', 'gmm_line')
            .attr('class', 'gmm_line_'+name)
            .attr('id', 'gmm2_line_'+name)
            .attr('transform', 'translate(0,-31)')
            .attr('fill', 'none')
            .attr('stroke', 'red')
    }

    async getAndDrawGatingGMM(name) {
        await this.getGatingGMM(name);

        const channelID = this.gatingIDs[name];
        const autoBtn = document.getElementById(`auto-btn-gating_${channelID}`);
        autoBtn.classList.remove("auto-loading")

        this.drawGatingGMM(name);
    }

    async updateGMM(selection_ids) {
        for (let name in this.hasGatingGMM) {
            await this.getGatingGMM(name, selection_ids=selection_ids);

            const fullName = this.dataLayer.getFullChannelName(name);
            const { xDomain, yDomain } = this.histogramData(fullName);
            const packet = this.hasGatingGMM[name];
            let gmm1Data = packet['gmm_1'];
            let gmm2Data = packet['gmm_2'];
            const gmm1_yMax = Math.max(...gmm1Data.map(obj => obj.y));
            const gmm2_yMax = Math.max(...gmm2Data.map(obj => obj.y));
            const yMax = Math.max(gmm1_yMax, gmm2_yMax);
            const gmm_yDomain = [yMax, 0]

            const gatingListEl = document.getElementById("csv_gating_list");
            const swidth = gatingListEl.getBoundingClientRect().width;

            let xScale = d3.scaleLinear()
                .domain(xDomain)
                .range([0, swidth - 73])

            let yScale = d3.scaleLinear()
                .domain(gmm_yDomain)
                .range([0, 25])

            let line = d3.line()
                .x(d => xScale(d.x))
                .y(d => yScale(d.y))
                .curve(d3.curveMonotoneX)

            let channel_gmm1 = d3.select('#gmm1_line_'+name)
            let channel_gmm2 = d3.select('#gmm2_line_'+name)
            channel_gmm1.data([gmm1Data]).transition().duration(1000).attr('d', line)
            channel_gmm2.data([gmm2Data]).transition().duration(1000).attr('d', line)
        }
    }

    /**
     * @function resetGatingList - resets all channels in the list to its initial range
     */
    resetGatingList() {
        let gatingList = Object.keys(this.selections);
        _.each(gatingList, col => {
            let shortName = this.dataLayer.getShortChannelName(col);
            let channelID = this.gatingIDs[shortName];
            let gating_selector = `#csv_gating-slider_${channelID}`;
            document.querySelector(gating_selector).click();
        });
    };

    /**
     * @function moveSliderHandles - move the slider handles and input fields so that input fields don't overlap when handles are close
     *
     * @param slider - the slider affected
     * @param vals - holds the new positions
     * @param name - the name of the slider
     * @param eventName - the name of the event
     */
    moveSliderHandles(slider, vals, name, eventName) {
        const fullName = this.dataLayer.getFullChannelName(name);
        const channelID = this.gatingIDs[name];
        this.gating_channels[fullName] = vals;
        this.selections[fullName] = vals;
        slider.silentValue(vals);
        const diff = Math.abs(vals[1] - vals[0]);
        const total = Math.abs(slider.max() - slider.min());
        const percentage = diff / total;
        if (percentage < 0.15){
            console.log('slider handles overlap..do something');
            d3.select('#c_foreignObject_'  + channelID + 1).attr('x', 5);
        }else{
            d3.select('#c_foreignObject_'  + channelID + 1).attr('x', -25);
        }
        const packet = this.selections;
        this.eventHandler.trigger(CSVGatingList.events[eventName], packet);
    }

    /**
     * @function dist - caclulates the distance between two rects
     * @param el1
     * @param el2
     * @param buffer
     * @returns {number}
     */
    dist(el1, el2, buffer) {
        var rect1 = el1.getBoundingClientRect();
        var rect2 = el2.getBoundingClientRect();
        return rect2.left - rect1.right;
    }
}

//resize sliders, etc on window change
window.addEventListener("resize", () => {
    const { csv_gatingList } = __minervaAnalysis;
    if (typeof csv_gatingList != "undefined" && csv_gatingList) {
        csv_gatingList.sliders.forEach((slider, name) => {
            let gatingID = csv_gatingList.gatingIDs[name]
            d3.select('div#csv_gating-slider_' + gatingID).select('svg').remove();
            let fullName = csv_gatingList.dataLayer.getFullChannelName(name);
            let sliderRange = [csv_gatingList.databaseDescription[fullName].min, csv_gatingList.databaseDescription[fullName].max];
            const gatingListEl = document.getElementById("csv_gating_list");
            if (gatingListEl) {
                const swidth = gatingListEl.getBoundingClientRect().width;
                csv_gatingList.addSlider(name, swidth, sliderRange, slider.value());
                if (csv_gatingList.hasGatingGMM[name]) {
                    csv_gatingList.drawGatingGMM(name);
                }
            }
        });
    }
});

//hide gating control panel when scrolled down to access all channels..
// $(document).ready(function()
// {
//    $('#csv_gating_list').scroll(function()
//    {
//       var div = $(this);
//       if (div[0].scrollHeight - div.scrollTop() < div.height()+10)
//       {
//             $('#gating_controls_panel').hide();
//       }else{
//             $('#gating_controls_panel').show();
//       }
//    });
// });

//static vars: events introduced in this class and used across the app
CSVGatingList.events = {
    GATING_BRUSH_MOVE: "GATING_BRUSH_MOVE",
    GATING_BRUSH_END: "GATING_BRUSH_END",
    GATING_COLOR_TRANSFER_CHANGE_MOVE: "GATING_TRANSFER_CHANGE_MOVE",
    GATING_COLOR_TRANSFER_CHANGE: "GATING_TRANSFER_CHANGE",
    GATING_CHANNELS_CHANGE: "GATING_CHANNELS_CHANGE",
    RESET_GATINGLIST: "RESET_GATINGLIST"
};
