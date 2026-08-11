/**
 * @class ChannelList - A view to select and deselect active image channels, set color and transfer functions
 */
class ChannelList {

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
        this.selections = [];
        this.hasChannelGMM = {};
        this.ranges = {};
        this.sliders = new Map();
        this.image_channels = {};
        this.sel = {};
        this.currentChannels = {};
        this.rangeConnector = {};
        this.colorConnector = {};
        this.channelIDs = {};
        this.createColorPicker();
        this.container = d3.select("#channel_list");
    }

    /**
     * A color picker that can be activated on each channel
     */
    createColorPicker(){
        //  create a color picker
        this.rainbow = rainbow();
        this.colorTransferHandle = null;
        d3.select(document.body)//add it to body
            .call(this.rainbow
                .on('save', (color, x) => {
                    let data = this.colorTransferHandle.datum();
                    let packet = {
                        name: data.name,  // cell name :  string
                        type: data.color,         // white, black :  string
                        color: d3.rgb(color),     // parse using d3.rgb(color) : https://github.com/d3/d3-color#rgb
                    };
                    this.eventHandler.trigger(ChannelList.events.COLOR_TRANSFER_CHANGE, packet);
                    this.colorTransferHandle.style('fill', color);
                })
                .on('close', () => this.colorTransferHandle = null));
    }

    /**
     * Removes a channel form the current selection
     * @param name - the name of the channel to remove
     */
    removeChannel(name) {
        // Update selections
        delete this.sel[this.dataLayer.getFullChannelName(name)];

        // Trigger
        // this.eventHandler.trigger(ChannelList.events.CHANNEL_SELECT, this.sel);
    }

    /**
     * Selects a channel as active and adds the respective viual components to the channel panel in the list view
     * @param name - the channel to set and display as selected
     */
    selectChannel(name) {
        let fullName = this.dataLayer.getFullChannelName(name);
        let channelIdx = imageChannels[fullName];
        let channelID = this.channelIDs[name];

        if (!this.rangeConnector[channelIdx]) {
            let defaultRange = this.dataLayer.imageBitRange;
            this.sliders.get(name).value([defaultRange[0], defaultRange[1]]);
        }

        if (!this.colorConnector[channelIdx]) {
            let rgbColor = `rgb(255, 255, 255)`;
            let selectorColor = `#color_${channelID}`;
            let selectorDoc = document.querySelector(selectorColor);
            selectorDoc.style.fill = rgbColor;
        }

        if (!(name in this.hasChannelGMM)) {
            this.getAndDrawChannelGMM(name);
        }

        // Update selections
        this.selections.push(name);
        this.sel[fullName] = this.image_channels[name];

        // Trigger
        // this.eventHandler.trigger(ChannelList.events.CHANNEL_SELECT, this.sel);
    }

    /**
     * triggerss an event that distributes the selected channel information.
     */
    triggerChannelSelect() {
        // Trigger
        this.eventHandler.trigger(ChannelList.events.CHANNEL_SELECT, this.sel);
    }

    /**
     * initializes the view (channel list)
     * @param dd - database description
     * @returns {Promise<void>}
     */
    init(dd) {
        this.databaseDescription = dd;
        this.rainbow.hide();
        // Hide the Loader
        document.getElementById('channel_list_loader').style.display = "none";
        let channel_list = document.getElementById("channel_list");
        let list = document.createElement("ul");
        list.classList.add("list-group")
        channel_list.appendChild(list)
        // Will show the picker when you click on a color rect
        let showPicker = e => {
            this.colorTransferHandle = d3.select(e.target);
            let color = this.colorTransferHandle.style('fill');
            this.rainbow.show(e.clientX, e.clientY);
            this.rainbow.set(d3.hsl(color));

        };
        // Draws rows in the channel list
        _.each(this.columns, (column, index) => {
            let channelID = `channel_${index}`;
            this.channelIDs[column] = channelID;
            // div for each row in channel list
            let listItemParentDiv = document.createElement("div");
            listItemParentDiv.classList.add("list-group-item");
            listItemParentDiv.classList.add("container");
            listItemParentDiv.classList.add("channel-list-content");
            // row
            let row = document.createElement("div");
            row.classList.add("row");
            listItemParentDiv.appendChild(row);
            // row
            let row2 = document.createElement("div");
            row2.classList.add("row");
            listItemParentDiv.appendChild(row2);

            // column within row that contains the name of the channel
            let nameCol = document.createElement("div");
            nameCol.classList.add("col-md-4");
            nameCol.classList.add("channel-col");
            row.appendChild(nameCol);

            // column within row that Contains the slider for the channel
            let sliderCol = document.createElement("div");
            sliderCol.classList.add("col-md-12");
            sliderCol.classList.add("channel-slider");
            sliderCol.setAttribute('id', "channel-slider_" + channelID)
            row2.appendChild(sliderCol);

            // column within row that contains svg for color pickers
            let svgCol = document.createElement("div");
            svgCol.classList.add("col-md-4");
            svgCol.classList.add("ml-auto");
            svgCol.classList.add("channel-col");
            svgCol.classList.add("channel-svg-wrapper");
            svgCol.classList.add("col-svg-wrapper");
            row.appendChild(svgCol);

            let colorLabel = document.createElement("span");
            colorLabel.textContent = "Color:";
            svgCol.appendChild(colorLabel);

            let svg = d3.select(svgCol)
                .append("svg")
                .attr("width", 15)
                .attr("height", 15)
            svg.selectAll("circle")
                .data([{"color": "white", "name": column}])
                .enter().append("rect")
                .attr("id", "color_" + channelID)
                .attr("class", "color-transfer")
                .attr("cursor", "pointer")
                .attr("stroke", "#757575")
                .attr("fill", d => d.color)
                .attr("width", "10")
                .attr("height", "10")
                .attr("rx", "2")
                .attr("ry", "2")
                .attr("x", "5")
                .attr("y", "2")
                .on('pointerup', showPicker);
            //<rect class="color-transfer" cursor="pointer" stroke="#757575" fill="black" width="10" height="10" rx="2" ry="2" x="-5" y="4.725680443548387" transform="translate(65,0)"></rect>
            svgCol.style.display = "none";

            let autoCol = document.createElement("div");
            autoCol.classList.add("col-md-4");
            autoCol.classList.add("ml-auto");
            autoCol.classList.add("image-auto")
            autoCol.setAttribute('id', "image-auto_" + channelID)
            autoCol.classList.add("channel-col");
            autoCol.classList.add("channel-svg-wrapper");
            row.appendChild(autoCol);

            let autoBtn = document.createElement("button");
            autoBtn.classList.add('auto-btn');
            autoBtn.classList.add('auto-loading');
            autoBtn.setAttribute('id', "auto-btn_" + channelID);
            autoBtn.textContent = "auto";
            const clickHandler = this.auto_channel.bind(this, column);
            autoBtn.addEventListener("click", clickHandler);

            autoCol.appendChild(autoBtn);
            autoBtn.addEventListener("click", e => e.stopPropagation());
            d3.select(autoCol).style('display', "none");

            let channelName = document.createElement("span");
            channelName.classList.add("channel-name");
            channelName.classList.add("list-button");
            channelName.textContent = column;
            nameCol.appendChild(channelName);

            listItemParentDiv.addEventListener("click", e => this.toggleChannelPanel(e, svgCol));
            list.appendChild(listItemParentDiv);

            //add and hide channel sliders (will be visible when channel is active)
            let fullName = this.dataLayer.getFullChannelName(column)
            let sliderMin = this.databaseDescription[fullName]['image_min']
            let sliderMax = this.databaseDescription[fullName]['image_max']
            this.image_channels[column] = [sliderMin, sliderMax];
            const channelListEl = document.getElementById("channel_list");
            const swidth = channelListEl.getBoundingClientRect().width;
            let sliderRange = this.addSlider(column, swidth, [sliderMin, sliderMax]);
            d3.select('div#channel-slider_' + channelID).style('display', "none");

        });

        let arrow_db = document.getElementById('channels_upload_icon_db')
        arrow_db.onclick = async () => {
            await this.applyChannels('db');
        }

        // Load a channel CSV that lives on the OMERO image (was: a local file
        // picker uploading to the server's disk). Any CSV attached to the image
        // is offered, not just the ones Gater wrote, so a table produced by
        // another tool can be loaded too.
        let arrow = document.getElementById('channels_upload_icon')
        arrow.onclick = async () => {
            await this.loadChannelCsvFromOmero();
        }

        this.addDownloadEvents();
    }

    /**
     * @function applyChannels
     * Applies channel settings (saved render state, or rows parsed from a CSV
     * on OMERO) to the channel list.
     * @param {String} source 'csv' to apply the rows passed in, anything else
     *                        to fetch this datasource's saved state
     * @param {Array}  providedChannels rows to apply when source === 'csv'
     * @returns {Number} how many rows matched a channel in this image. A CSV
     *                   from another image can parse fine yet match nothing, so
     *                   the caller needs to be able to say so.
     */
    async applyChannels(source, providedChannels) {
        let channels;
        if (source === 'csv') {
            channels = providedChannels || [];
        } else {
            channels = await this.dataLayer.getSavedChannelList();
        }
        let applied = 0;

        let defaultRange = this.dataLayer.imageBitRange;

        // Apply the saved/uploaded channel config IDEMPOTENTLY, so it is safe to
        // run repeatedly -- the on-open auto-restore AND a later "Load from
        // OMERO" click both land on the same state. For each channel we set its
        // range + color connectors, then bring its active state to the saved
        // value by toggling ONLY when it differs from the current one
        // (this.selections is the reliable active list). The old code toggled
        // unconditionally, so a second run flipped already-active channels OFF
        // (reset-to-default) and flashed default<->saved.
        _.each(channels, col => {
            if (!this.sliders.get(col.channel)) return;
            applied++;

            let fullName = this.dataLayer.getFullChannelName(col.channel);
            let channelIdx = imageChannels[fullName];
            let channelID = this.channelIDs[col.channel];
            let isActive = _.includes(this.selections, col.channel);

            // Range connector (channel_add reads this on activation).
            if (col.start > this.image_channels[col.channel][0] || col.end < this.image_channels[col.channel][1]) {
                this.rangeConnector[channelIdx] = [col.start / defaultRange[1], col.end / defaultRange[1]];
            } else {
                delete this.rangeConnector[channelIdx];
            }

            // Color connector MUST be a d3.rgb -- that is the shape channel_add /
            // updateChannelColors and the WebGL renderer expect (the color
            // picker stores d3.rgb). Storing a plain {r,g,b} object rendered as
            // default white: the actual "color not restored" bug.
            let hasColor = (col.r !== 255 || col.g !== 255 || col.b !== 255);
            if (hasColor) {
                let swatch = document.querySelector(`#color_${channelID}`);
                if (swatch) swatch.style.fill = `rgb(${col.r}, ${col.g}, ${col.b})`;
                this.colorConnector[channelIdx] = {color: d3.rgb(col.r, col.g, col.b)};
            } else {
                delete this.colorConnector[channelIdx];
            }

            if (col['channel_active']) {
                // Reflect the saved range on the slider.
                this.sliders.get(col.channel).value([col.start, col.end]);
                if (!isActive) {
                    // Activate: channel_add picks up the range + color connectors.
                    document.querySelector(`#channel-slider_${channelID}`).click();
                }
                // updateChannelColors only applies to an ALREADY-active channel,
                // so fire this after activation -- covers both fresh-activate and
                // recolor-in-place (button click on an already-active channel).
                if (hasColor) {
                    this.eventHandler.trigger(ChannelList.events.COLOR_TRANSFER_CHANGE, {
                        name: col.channel,
                        type: 'white',
                        color: d3.rgb(col.r, col.g, col.b)
                    });
                }
            } else if (isActive) {
                // Saved as inactive but currently active -> turn it off.
                document.querySelector(`#channel-slider_${channelID}`).click();
            }
        })
        return applied;
    }


    /**
     * @function loadChannelCsvFromOmero
     * Lists the CSV attachments on this datasource's OMERO image, lets the user
     * pick one, then applies it to the channel list.
     */
    async loadChannelCsvFromOmero() {
        let csvs;
        try {
            // Only channel CSVs Gater saved: other attachments on the image are
            // not readable as a channel list, so offering them just invites an
            // error.
            csvs = await this.dataLayer.listOmeroChannelCsvs(true);
        } catch (e) {
            alert(e.message);
            return;
        }
        if (!csvs.length) {
            alert("No channel CSVs have been saved to this image yet.\n\n" +
                "Use \"Save Channels as a CSV on OMERO\" to create one.");
            return;
        }

        const chosen = await GaterCsvDialogs.pickOmeroCsv(csvs, {
            title: 'Load channels from a CSV on OMERO',
            subtitle: 'Channel CSVs saved to this image.'
        });
        if (!chosen) return;                       // cancelled

        let rows;
        try {
            rows = await this.dataLayer.getOmeroChannelCsvValues(chosen.id);
        } catch (e) {
            alert(e.message);
            return;
        }

        const applied = await this.applyChannels('csv', rows);
        if (!applied) {
            // Parsed fine but nothing matched -- almost always a CSV whose
            // channel names come from a different image.
            alert(`Loaded "${chosen.name}", but none of its ${rows.length} ` +
                `channel name(s) match this image's channels. Nothing changed.`);
        }
    }


    /**
     * @function saveChannelCsvToOmero
     * Asks the user what to call the CSV (defaulting to <datasource>_channels.csv,
     * warning if that name is already taken), then writes it to the OMERO image.
     */
    async saveChannelCsvToOmero() {
        // Re-entry guard: one CSV save at a time (the server enforces this too,
        // since a queued save ties up a thread from a small pool).
        if (this._csvSaveInFlight) return;
        this._csvSaveInFlight = true;
        try {
            await this._saveChannelCsvToOmero();
        } finally {
            this._csvSaveInFlight = false;
        }
    }


    async _saveChannelCsvToOmero() {
        // Existing names drive the "already exists" warning. If the list can't
        // be fetched we still let the save proceed -- the server re-checks and
        // returns 409, which is handled below.
        let existing = [];
        try {
            existing = await this.dataLayer.listOmeroChannelCsvs();
        } catch (e) {
            console.log("Could not list existing CSVs on OMERO", e);
        }

        const chosen = await this.promptCsvName(
            this.dataLayer.defaultChannelCsvName(), existing);
        if (!chosen) return;                       // cancelled

        const save = async (overwrite) => {
            return this.dataLayer.saveChannelsCsvToOmero(
                imageChannelsIdx,
                this.currentChannels,
                this.colorConnector,
                this.rangeConnector,
                this.image_channels,
                chosen.name,
                overwrite
            );
        };

        const busy = GaterCsvDialogs.showSaveBusy(
            `Saving "${chosen.name}" to OMERO`, 'Please wait.');
        try {
            const res = await save(chosen.overwrite);
            busy.close();
            alert(`Saved "${res.name || chosen.name}" to OMERO`);
        } catch (e) {
            busy.close();
            if (e.exists) {
                // Someone created this name between listing and saving.
                const go = await GaterCsvDialogs.confirmDialog(
                    'A CSV with this name already exists', e.message, 'Overwrite');
                if (go) {
                    const busy2 = GaterCsvDialogs.showSaveBusy(
                        `Replacing "${chosen.name}" on OMERO`, 'Please wait.');
                    try {
                        const res = await save(true);
                        alert(`Saved "${res.name || chosen.name}" to OMERO`);
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
     * @function promptCsvName
     * Modal asking what to call the CSV. Warns live as the user types when the
     * name is already taken, so the choice between overwriting and renaming is
     * made in one step rather than via a second confirm dialog.
     * Resolves {name, overwrite} or null if dismissed.
     */
    promptCsvName(defaultName, existing) {
        return new Promise(resolve => {
            // A CSV is known by ONE name -- filename, description and collision
            // key alike -- so what the user sees here is exactly what OMERO
            // will show. Shared with the gating panel so the two cannot drift.
            const sanitize = GaterCsvDialogs.sanitize;
            const canonical = GaterCsvDialogs.canonical;

            // Only Gater's own CSVs can be replaced; a same-named attachment
            // from elsewhere is in a different namespace, so saving would leave
            // two files sharing a name. Warn differently for each case. Compare
            // canonically, so a file saved under an older spelling (spaces kept
            // in the description) still registers as the same name.
            const mine = new Set((existing || []).filter(c => c.is_gater)
                .map(c => canonical(c.description || c.name)));
            const theirs = new Set((existing || []).filter(c => !c.is_gater)
                .map(c => canonical(c.name)));

            const overlay = document.createElement('div');
            overlay.className = 'gater-picker-overlay';

            const box = document.createElement('div');
            box.className = 'gater-picker';
            box.innerHTML =
                '<h4>Save channels as a CSV on OMERO</h4>' +
                '<p class="gater-picker-sub">Name the file. Saving a name that ' +
                'already exists replaces that file.</p>';

            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'gater-picker-input';
            input.value = sanitize(defaultName);
            box.appendChild(input);

            const hint = document.createElement('p');
            hint.className = 'gater-picker-hint';
            hint.textContent = 'Spaces become “_” — OMERO stores the file under ' +
                'this exact name.';
            box.appendChild(hint);

            const warn = document.createElement('p');
            warn.className = 'gater-picker-warn';
            warn.style.display = 'none';
            box.appendChild(warn);

            const actions = document.createElement('div');
            actions.className = 'gater-picker-actions';
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'gater-picker-cancel';
            cancel.textContent = 'Cancel';
            const saveBtn = document.createElement('button');
            saveBtn.type = 'button';
            saveBtn.className = 'gater-picker-save';
            actions.appendChild(cancel);
            actions.appendChild(saveBtn);
            box.appendChild(actions);

            let willOverwrite = false;
            function refresh() {
                // Substitute disallowed characters in place as the user types,
                // so the box always shows the name that will be written.
                GaterCsvDialogs.sanitizeInputInPlace(input);
                const n = canonical(input.value);
                saveBtn.disabled = !n;
                if (n && mine.has(n)) {
                    willOverwrite = true;
                    warn.textContent =
                        `"${n}" already exists on this image. Saving will replace it — ` +
                        `use a different name to keep both.`;
                    warn.style.display = 'block';
                    saveBtn.textContent = 'Overwrite';
                } else if (n && theirs.has(n)) {
                    willOverwrite = false;
                    warn.textContent =
                        `An attachment called "${n}" already exists on this image but ` +
                        `was not written by Gater, so it will not be replaced — you ` +
                        `would end up with two files of the same name.`;
                    warn.style.display = 'block';
                    saveBtn.textContent = 'Save anyway';
                } else {
                    willOverwrite = false;
                    warn.style.display = 'none';
                    saveBtn.textContent = 'Save';
                }
            }

            function submit() {
                const n = canonical(input.value);
                if (!n) return;
                close();
                resolve({name: n, overwrite: willOverwrite});
            }
            function close() {
                document.removeEventListener('keydown', onKey);
                overlay.remove();
            }
            function onKey(e) {
                if (e.key === 'Escape') { close(); resolve(null); }
            }

            input.addEventListener('input', refresh);
            input.addEventListener('keydown', e => {
                if (e.key === 'Enter') { e.preventDefault(); submit(); }
            });
            saveBtn.onclick = submit;
            cancel.onclick = () => { close(); resolve(null); };
            overlay.onclick = e => {
                if (e.target === overlay) { close(); resolve(null); }
            };
            document.addEventListener('keydown', onKey);

            overlay.appendChild(box);
            document.body.appendChild(overlay);
            refresh();
            input.focus();
            // Select the stem, not the .csv, so typing replaces just the name.
            const dot = input.value.lastIndexOf('.');
            input.setSelectionRange(0, dot > 0 ? dot : input.value.length);
        });
    }



     auto_channel(name) {
        let fullName = this.dataLayer.getFullChannelName(name);
        if (!(name in this.hasChannelGMM)) {
          return
        }
        let vmin = this.hasChannelGMM[name]['vmin'];
        let vmax = this.hasChannelGMM[name]['vmax'];
        this.sliders.get(name).value([vmin, vmax]);

        let channelIdx = imageChannels[fullName];
        let defaultRange = this.dataLayer.imageBitRange;
        this.rangeConnector[channelIdx] = [vmin / defaultRange[1], vmax / defaultRange[1]];
    }


    /**
     * @function addDownloadEvents
     *  Adds event listeners to upload/download buttons
     */
    addDownloadEvents() {
        // Write the channel list as a NAMED CSV attachment on the OMERO image
        // (was: a browser download). Same content as "Save Channels to OMERO",
        // but as text/csv so it can be read in OMERO.web or a spreadsheet. The
        // user names the file, so an image can carry several.
        const channels_download_icon = document.querySelector('#channels_download_icon');
        channels_download_icon.addEventListener('click', async () => {
            await this.saveChannelCsvToOmero();
        });

        const channels_download_icon_db = document.querySelector('#channels_download_icon_db');
        channels_download_icon_db.addEventListener('click', () => {
            this.dataLayer.saveChannelList(
                imageChannelsIdx,
                this.currentChannels,
                this.colorConnector,
                this.rangeConnector,
                this.image_channels
            );
            alert("Saved Channels to OMERO");
        });
    }


    /**
     * @function toggleChannelPanel
     *
     * @param {Event} event - The event
     * @param svgCol - the column to expand or collapse
     */
    toggleChannelPanel(event, svgCol) {

        // If you clicked on the svg, ignore this behavior
        if (event.target.closest("svg")) {
            return;
        }

        // Get info
        let parent = event.target.closest(".list-group-item");
        let name = parent.querySelector('.channel-name').textContent;
        let channelID = this.channelIDs[name];
        let status = !parent.classList.contains("active");

        // If active - else inactive
        if (status) {

            // Don't add channel is the max are selected
            if (_.size(this.selections) >= this.maxSelections) {
                return;
            }

            // Update properties and add slider
            d3.select(parent).classed("active", true);
            svgCol.style.display = "block";
            d3.select('div#channel-slider_' + channelID).style('display', "block")
            d3.select('div#image-auto_' + channelID).style('display', "block");

            // Add channel
            this.selectChannel(name);

        } else {
            // Clear panel visibility
            // clearOut();
            this.selections = _.pull(this.selections, name);

            // Remove channel and rerender
            this.removeChannel(name);

            // Hide
            d3.select(parent).classed("active", false);
            svgCol.style.display = "none";
            d3.select('div#channel-slider_' + channelID).style('display', "none")
            d3.select('div#image-auto_' + channelID).style('display', "none");

            // Trigger viewer cleanse
            // this.eventHandler.trigger(ChannelList.events.CHANNELS_CHANGE, this.selections);
        }

        //
        let selectionsHeaderDiv = document.getElementById("selected-channels-header-div");
        if (selectionsHeaderDiv) {
            if (_.size(this.selections) >= this.maxSelections) {
                selectionsHeaderDiv.classList.add('bold-selections-header');
            } else {
                selectionsHeaderDiv.classList.remove('bold-selections-header');
            }
            let packet = {selections: this.selections, name, status};
            // console.log('channels_change', packet);
            document.getElementById("num-selected-channels").textContent = _.size(this.selections);

            // Trigger event
            this.eventHandler.trigger(ChannelList.events.CHANNELS_CHANGE, packet);

        }
    }

    /**
    add a slider to a channel
    @param {} data - the min and max range of the slider
    @param {} activeRange - the predefined values for the lower and upper handle
    @param {String} name - the name of the slider (used as part of the id)
    @param {} swidth - the pixel width of the slider
     */
    addSlider(name, swidth, activeRange) {

        const f = (d3.format('.2%'))
        let channelID = this.channelIDs[name];
        const fullName = this.dataLayer.getFullChannelName(name);
        const { xDomain, yDomain, histogramData} = this.histogramData(fullName); 
        let data_min = this.databaseDescription[fullName]['image_min'];
        let data_max = this.databaseDescription[fullName]['image_max'];

        //add range slider row content
        const sliderSimple = d3.sliderBottom(d3.scaleLog())
            .min(data_min)
            .max(data_max)
            .width(swidth - 75)//.tickFormat(d3.format("s"))
            .fill('orange')
            .on('onchange', (range) => {
                const v0 = Math.round(range[0]);
                const v1 = Math.round(range[1]);
                d3.select('#slider-input' + channelID + 0).attr('value', v0);
                d3.select('#slider-input' + channelID + 0).property('value', v0);
                d3.select('#slider-input' + channelID + 1).attr('value', v1);
                d3.select('#slider-input' + channelID + 1).property('value', v1);
                this.moveSliderHandles(sliderSimple, [v0, v1], name);
            })
            .ticks(5)
            .default([Math.round(activeRange[0]), Math.round(activeRange[1])])
            .handle(
                d3.symbol()
                    .type(d3.symbolCircle)
                    .size(100)
            )
            .tickValues([])

        this.sliders.set(name, sliderSimple);

        //create the slider svg and call the slider
        var gSimple = d3
            .select('#channel-slider_' + channelID)
            .append('svg')
            .attr('class', 'svgslider')
            .attr('id', 'channel-slider_svg_' + channelID)
            .attr('width', swidth)
            .attr('height', 80)
            .append('g')
            .attr('transform', 'translate(20,40)');

        let xScale = d3.scaleLinear()
            .domain(xDomain)
            .range([0, swidth - 73])

        let yScale = d3.scaleLinear()
            .domain(yDomain)
            .range([0, 23])

        let line = d3.line()
            .x(d => xScale(d.x))
            .y(d => yScale(d.y))
            .curve(d3.curveMonotoneX)

        const lines = gSimple.selectAll('.image_distribution_line');
        const paths = lines.data([histogramData]).enter().append('path');
        paths
        .attr('d', line)
        .attr('class', 'image_distribution_line')
        .attr('transform', 'translate(0,-31)')
        .attr('fill', 'none')

        gSimple.call(sliderSimple);

        //slider value to be displayed closer to the slider than default
        d3.selectAll('.parameter-value').select('text')
            .attr("y", 10);

        //both handles
        const { sliders } = this;
        const handles = d3.select('#channel-slider_' + channelID).selectAll(".parameter-value");
        handles.each(function(d, i) {
            d3.select(this).append("foreignObject")
            .attr('id', 'foreignObject_' + channelID + i)
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
            .attr('id', 'slider-input' + channelID + i)
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
              moveSliderHandles(sliderSimple, vals, name);
          }
        })

        return sliderSimple;
    };

    histogramData(fullName) {
        const histogramData = this.databaseDescription[fullName].image_histogram;
        const xMin = Math.min(...histogramData.map(e => e.x));
        const xMax = Math.max(...histogramData.map(e => e.x));
        const yMax = Math.max(...histogramData.map(e => e.y));
        return {
            histogramData,
            xDomain: [xMin, xMax],
            yDomain: [yMax, 0],
        };
    }

    async getAndDrawChannelGMM(name) {
        const fullName = this.dataLayer.getFullChannelName(name);
        const packet = await this.dataLayer.getChannelGMM(fullName);
        const channelID = this.channelIDs[name];
        const autoBtn = document.getElementById(`auto-btn_${channelID}`);
        autoBtn.classList.remove("auto-loading")
        this.hasChannelGMM[name] = packet;

        this.drawChannelGMM(name);
    }

    drawChannelGMM(name) {
        let channelID = this.channelIDs[name];
        let packet = this.hasChannelGMM[name];
        let channel_gmm1Data = packet['image_gmm_1'];
        let channel_gmm2Data = packet['image_gmm_2'];
        let channel_gmm3Data = packet['image_gmm_3'];
        const fullName = this.dataLayer.getFullChannelName(name);
        const { xDomain, yDomain } = this.histogramData(fullName); 

        const channelListEl = document.getElementById("channel_list");
        const swidth = channelListEl.getBoundingClientRect().width;

        let xScale = d3.scaleLinear()
            .domain(xDomain)
            .range([0, swidth - 73])

        let yScale = d3.scaleLinear()
            .domain(yDomain)
            .range([0, 23])

        let line = d3.line()
            .x(d => xScale(d.x))
            .y(d => yScale(d.y))
            .curve(d3.curveMonotoneX)

        let gSimple = d3.select('#channel-slider_svg_' + channelID + ' g')

        gSimple.selectAll('.image_gmm1_line')
            .data([channel_gmm1Data])
            .enter()
            .append('path')
            .attr('d', line)
            .attr('class', 'gmm_line')
            .attr('class', 'gmm_line_'+name)
            .attr('transform', 'translate(0,-31)')
            .attr('fill', 'none')
            .attr('stroke', 'green')

        gSimple.selectAll('.image_gmm2_line')
            .data([channel_gmm2Data])
            .enter()
            .append('path')
            .attr('d', line)
            .attr('class', 'gmm_line')
            .attr('class', 'gmm_line_'+name)
            .attr('transform', 'translate(0,-31)')
            .attr('fill', 'none')
            .attr('stroke', 'blue')

        gSimple.selectAll('.image_gmm3_line')
            .data([channel_gmm3Data])
            .enter()
            .append('path')
            .attr('d', line)
            .attr('class', 'gmm_line')
            .attr('class', 'gmm_line_'+name)
            .attr('transform', 'translate(0,-31)')
            .attr('fill', 'none')
            .attr('stroke', 'red')
    }

    /**
     * @function moveSliderHandles - move the slider handles and input fields so that input fields don't overlap when handles are close
     *
     * @param slider - the slider affected
     * @param vals - holds the new positions
     * @param name - the name of the slider
     */
    moveSliderHandles(slider, vals, name){
        let channelID = this.channelIDs[name];
        this.image_channels[name] = vals;
        slider.silentValue(vals);
        if (vals[1] - vals[0] < 0.41){
            console.log('slider handles overlap..do something');
            d3.select('#foreignObject_'  + channelID + 1).attr('x', 5);
        }else{
            d3.select('#foreignObject_'  + channelID + 1).attr('x', -25);
        }
        const packet = {name: name, dataRange: [...vals]};
        this.eventHandler.trigger(ChannelList.events.BRUSH_MOVE, packet);
    }

    /**
     */
    resetChannelList() {
        let channelList = _.clone(this.selections);
        _.each(channelList, col => {
            let channelID = this.channelIDs[col];
            let channel_selector = `#channel-slider_${channelID}`;
            document.querySelector(channel_selector).click();
        });
    }
}

/**
 * on window resize we re-initialize (this should be better handled with an update pattern)
 */
window.addEventListener("resize", function () {
    const { channelList } = __minervaAnalysis;
    if (typeof channelList != "undefined" && channelList) {
        channelList.sliders.forEach((slider, name) => {
            let channelID = channelList.channelIDs[name]
            d3.select('div#channel-slider_' + channelID).select('svg').remove();
            const channelListEl = document.getElementById("channel_list");
            if (channelListEl) {
                const swidth = channelListEl.getBoundingClientRect().width;
                channelList.addSlider(name, swidth, slider.value());
                if (channelList.hasChannelGMM[name]) {
                    channelList.drawChannelGMM(name);
                }
            }
      });
    }
});

//static vars: events introduced in this class and used across the app
ChannelList.events = {
    BRUSH_MOVE: "BRUSH_MOVE",
    COLOR_TRANSFER_CHANGE_MOVE: "COLOR_TRANSFER_CHANGE_MOVE",
    COLOR_TRANSFER_CHANGE: "COLOR_TRANSFER_CHANGE",
    CHANNELS_CHANGE: "CHANNELS_CHANGE",
    CHANNEL_SELECT: "CHANNEL_SELECT",
    RESET_LISTS: "RESET_LISTS"
};
