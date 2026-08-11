//todo add crossfilter stuff here... build some lensingFilters and sorters for individual and combined dimensions

class DataLayer {

    constructor(config, imageChannels) {
        var that = this;
        //vars and consts
        this.config = config;
        //all image channels
        this.imageChannels = imageChannels;

        this.imageBitRange = [0, 65536];
        //selections
        this.currentSelection = new Map();
        //x,z coords
        this.x = this.config["featureData"][dataSrcIndex]["xCoordinate"];
        this.y = this.config["featureData"][dataSrcIndex]["yCoordinate"];
        this.phenotypes = [];
    }

    async init() {
        try {
            await fetch('/init_database?' + new URLSearchParams({
                datasource: datasource
            }))

        } catch (e) {
            console.log("Error Initializing Dataset", e);
        }
    }

    async getRow(row) {
        try {
            let response = await fetch('/get_database_row?' + new URLSearchParams({
                row: row,
                datasource: datasource
            }))
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Getting Row", e);
        }
    }

    async getUploadedGatingCsvValues() {
        try {
            let response = await fetch('/get_uploaded_gating_csv_values?' + new URLSearchParams({
                datasource: datasource
            }))
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Getting Uploaded Gates", e);
        }
    }

    async getSavedGatingList() {
        try {
            let response = await fetch('/get_saved_gating_list?' + new URLSearchParams({
                datasource: datasource
            }))
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Getting Saved Gating List", e);
        }
    }

    // --- Channel CSV on OMERO --------------------------------------------
    // The channel CSV is an attachment on the datasource's OMERO image, not a
    // file on disk. These throw on failure carrying the server's own message,
    // so the caller can tell the user WHY (wrong columns, no OMERO image, ...).
    // The .catch on json() covers a non-JSON error page, which would otherwise
    // surface as an unhelpful parser error instead of the fallback text.

    async listOmeroChannelCsvs() {
        let response = await fetch('/list_omero_channel_csvs?' + new URLSearchParams({
            datasource: datasource
        }));
        let body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.error || 'Could not list CSVs on OMERO.');
        return body.csvs || [];
    }

    async getOmeroChannelCsvValues(annId) {
        let response = await fetch('/get_omero_channel_csv_values?' + new URLSearchParams({
            datasource: datasource,
            ann_id: annId
        }));
        let body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.error || 'Could not read that CSV.');
        return body;
    }

    async getSavedChannelList() {
        try {
            let response = await fetch('/get_saved_channel_list?' + new URLSearchParams({
                datasource: datasource
            }))
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Getting Saved Channel List", e);
        }
    }

    // --- Gating CSV on OMERO ----------------------------------------------
    // Mirrors the channel CSV methods: the gating panel's two outputs are
    // written as attachments on the OMERO image rather than downloaded.

    // kind: 'gating_csv' (gate ranges, loadable back) or 'gating_cells_csv'
    // (the per-cell export).
    defaultGatingCsvName(fullCsv) {
        return datasource + (fullCsv ? '_gated_cell_encodings.csv'
                                     : '_gated_channel_ranges.csv');
    }

    async listOmeroGatingCsvs(kind) {
        let response = await fetch('/list_omero_gating_csvs?' + new URLSearchParams({
            datasource: datasource,
            kind: kind || 'gating_csv'
        }));
        let body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.error || 'Could not list CSVs on OMERO.');
        return body.csvs || [];
    }

    async getOmeroGatingCsvValues(annId) {
        let response = await fetch('/get_omero_gating_csv_values?' + new URLSearchParams({
            datasource: datasource,
            ann_id: annId
        }));
        let body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.error || 'Could not read that CSV.');
        return body;
    }

    // A 409 comes back as an Error with .exists = true so the caller can offer
    // to overwrite instead of just reporting a failure.
    async saveGatingCsvToOmero(channels, selections, lassos, selection_ids, fullCsv, csvName, overwrite) {
        let response = await fetch('/save_gating_csv_to_omero', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                datasource: datasource,
                filter: selections,
                channels: channels,
                lassos: lassos,
                selection_ids: selection_ids,
                fullCsv: !!fullCsv,
                encoding: (document.getElementById('encoding') || {}).value,
                csv_name: csvName,
                overwrite: !!overwrite
            })
        });
        let body = await response.json().catch(() => ({}));
        if (!response.ok) {
            let err = new Error(body.error || 'Could not save the CSV to OMERO.');
            err.exists = !!body.exists;
            throw err;
        }
        return body;
    }

    async saveGatingList(channels, selections, lassos) {
        const self = this;
        try {
            let response = await fetch('/save_gating_list', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(
                    {
                        datasource: datasource,
                        filter: selections,
                        channels: channels,
                        lassos: lassos
                    }
                )
            });
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Saving Gating List", e);
        }
    }

    // Default name offered when saving a channel CSV. Lives here because the
    // datasource name is this layer's concern.
    defaultChannelCsvName() {
        return datasource + '_channels.csv';
    }

    // csvName is the user's chosen file name; overwrite must be true to replace
    // an existing one. A 409 comes back as an Error with .exists = true so the
    // caller can offer to overwrite instead of just reporting a failure.
    async saveChannelsCsvToOmero(map_channels, active_channels, list_colors, list_ranges, list_channels, csvName, overwrite) {
        let response = await fetch('/save_channels_csv_to_omero', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                datasource: datasource,
                map_channels: map_channels,
                active_channels: active_channels,
                list_colors: list_colors,
                list_ranges: list_ranges,
                list_channels: list_channels,
                csv_name: csvName,
                overwrite: !!overwrite
            })
        });
        let body = await response.json().catch(() => ({}));
        if (!response.ok) {
            let err = new Error(body.error || 'Could not save the CSV to OMERO.');
            err.exists = !!body.exists;
            err.name_taken = body.name;
            throw err;
        }
        return body;
    }

    async saveChannelList(map_channels, active_channels, list_colors, list_ranges, list_channels) {
        const self = this;
        try {
            let response = await fetch('/save_channel_list', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(
                    {
                        datasource: datasource,
                        map_channels: map_channels,
                        active_channels: active_channels,
                        list_colors: list_colors,
                        list_ranges: list_ranges,
                        list_channels: list_channels
                    }
                )
            });
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Saving Channel List", e);
        }
    }

    async getColumnDistributions(columns) {
        try {
            let response = await fetch('/get_column_distributions?' + new URLSearchParams({
                columns: columns,
                datasource: datasource
            }))
            let distributions = await response.json();
            return distributions;
        } catch (e) {
            console.log("Error Getting Nearest Cell", e);
        }
    }

    async getAllCells(start_keys, use_integer) {
        const dtype = use_integer ? 'integer' : 'float'
        const base_url = `/get_all_cells/${dtype}/?`
        try {
            const headers = new Headers();
            headers.append("Content-Type","application/octet-stream");
            headers.append("Content-Encoding","gzip");
            const response = await fetch(base_url + new URLSearchParams({
                start_keys: start_keys,
                datasource: datasource
            }), {
                headers: headers
            })
            return response.arrayBuffer();
        } catch (e) {
            console.log("Error Getting Gated Cell Ids", e);
        }
    }

    async getGatedCellIds(filter, start_keys) {
        try {
            let response = await fetch('/get_gated_cell_ids?' + new URLSearchParams({
                filter: JSON.stringify(filter),
                start_keys: start_keys,
                datasource: datasource
            }))
            let cellIds = await response.json();
            return cellIds;
        } catch (e) {
            console.log("Error Getting Gated Cell Ids", e);
        }
    }

    async getGatedCellIdsCustom(filter, start_keys) {
        try {
            // const start = performance.now()
            let response = await fetch('/get_gated_cell_ids_custom?' + new URLSearchParams({
                filter: JSON.stringify(filter),
                start_keys: start_keys,
                datasource: datasource
            }))
            let cellIds = await response.json();
            // const end = performance.now()
            // console.log(end - start)
            // console.log(cellIds)
            return cellIds;
        } catch (e) {
            console.log("Error Getting Gated Cell Ids", e);
        }
    }

    async getDatabaseDescription() {
        try {
            let response = await fetch('/get_database_description?' + new URLSearchParams({
                datasource: datasource
            }))
            let description = await response.json();
            return description;
        } catch (e) {
            console.log("Error Getting DB Description", e);
        }
    }

    async getChannelGMM(channel) {
        try {
            let response = await fetch('/get_channel_gmm?' + new URLSearchParams({
                channel: channel,
                datasource: datasource
            }))
            let packet_gmm = await response.json();
            return packet_gmm;
        } catch (e) {
            console.log("Error Getting Channel GMM", e);
        }
    }

    async getGatingGMM(channel, selection_ids) {
        try {
            let response = await fetch('/get_gating_gmm', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(
                    {
                        channel: channel,
                        datasource: datasource,
                        selection_ids: selection_ids
                    }
                )
            });
            let packet_gmm = await response.json();
            return packet_gmm;
        } catch (e) {
            console.log("Error Getting Gating GMM", e);
        }
    }

    async getChannelCellIds(sels) {
        try {
            let response = await fetch('/get_channel_cell_ids?' + new URLSearchParams({
                filter: JSON.stringify(sels),
                datasource: datasource
            }))
            let cellIds = await response.json();
            return cellIds;
        } catch (e) {
            console.log("Error Getting Channel Cell Ids", e);
        }
    }

    async getChannelNames(shortNames = true) {
        try {
            let response = await fetch('/get_channel_names?' + new URLSearchParams({
                datasource: datasource,
                shortNames: shortNames
            }))
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Getting Sample Row", e);
        }
    }

    async getColorScheme(refresh = false) {
        try {
            let response = await fetch('/get_color_scheme?' + new URLSearchParams({
                datasource: datasource,
                refresh: refresh
            }))
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Getting Sample Row", e);
        }
    }

    async getNearestCell(point_x, point_y) {
        try {
            let response = await fetch('/get_nearest_cell?' + new URLSearchParams({
                point_x: point_x,
                point_y: point_y,
                datasource: datasource
            }))
            let cell = await response.json();
            return cell;
        } catch (e) {
            console.log("Error Getting Nearest Cell", e);
        }
    }

    async getNeighborhood(maxDistance, x, y) {
        try {
            let response = await fetch('/get_neighborhood?' + new URLSearchParams({
                point_x: x,
                point_y: y,
                max_distance: maxDistance,
                datasource: datasource
            }))
            let neighborhood = await response.json();
            return neighborhood;
        } catch (e) {
            console.log("Error Getting Nearest Cell", e);
        }
    }

    async getNeighborhoodForCell(maxDistance, selectedCell) {
        return this.getNeighborhood(maxDistance, selectedCell[this.x], selectedCell[this.y]);
    }


    getCurrentSelection() {
        return this.currentSelection;
    }

    clearCurrentSelection() {
        this.currentSelection.clear();
    }

    getImageBitRange(float = false) {
        const self = this;
        if (!float) {
            return self.imageBitRange;
        } else {
            return [0.0, 1.0];
        }
    }

    addToCurrentSelection(item, allowDelete, clearPriors) {

        // delete item on second click
        if (allowDelete && this.currentSelection.has(item)) {
            this.currentSelection.delete(item);
            if (clearPriors) {
                this.currentSelection.clear();
            }

            // console.log('current selection size:', this.currentSelection.size);
            if (this.currentSelection.size > 0) {
                // console.log('id: ', this.currentSelection.values().next().value.id);
            }
            return;
        }

        // clear previous items
        if (clearPriors) {
            this.currentSelection.clear();
        }

        // add new item
        this.currentSelection.set(item[this.config.featureData[0].idField], item);

        // console.log('current selection size:', this.currentSelection.size);
        if (this.currentSelection.size > 0) {
            // console.log('id: ', this.currentSelection.values().next().value.id);
        }
    }


    addAllToCurrentSelection(items, allowDelete, clearPriors) {
        // console.log("update current selection")
        var that = this;
        let idField = this.config.featureData[0].idField
        that.currentSelection = new Map(items.map(i => [(i[idField]), i]));
        // console.log("update current selection done")
    }

    isImageFeature(key) {
        if (this.imageChannels.hasOwnProperty(key)
            && key != 'CellId' && key != 'id' && key != 'CellID' && key != 'ID' && key != 'Area') {
            return true;
        }
        return false;
    }

    getShortChannelName(fullname) {
        var shortname = fullname;
        this.config["imageData"].forEach(function (channel) {
            if (channel.fullname == fullname) {
                shortname = channel.name;
            }
        });
        return shortname;
    }

    getFullChannelName(shortname) {
        var fullname = shortname;
        this.config["imageData"].forEach(function (channel) {
            if (channel.name == shortname) {
                fullname = channel.fullname;
            }
        });
        return fullname;
    }

    async getMetadata() {
        try {
            let response = await fetch('/get_ome_metadata?' + new URLSearchParams({
                datasource: datasource
            }))
            let response_data = await response.json();
            return response_data;
        } catch (e) {
            console.log("Error Getting Metadata", e);
        }
    }

    /**
     * whether the current data is log transformed or not
     * @returns {boolean}
     */
    isTransformed(){
      if (this.config["featureData"][0]["isTransformed"]  !== undefined &&
          this.config["featureData"][0]["isTransformed"] == true){
          return true;
      }
      return false;
    }

     async getCellsInPolygon(points) {
        try {
            let response = await fetch('/get_cells_in_polygon', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(
                    {
                        datasource: datasource,
                        points: points,
                    }
                )
            });
            let cells = await response.json();
            return cells;
        } catch (e) {
            console.log("Error Getting Polygon Cells", e);
        }
    }

    async getCellsInLassos(list_lassos) {
        try {
            let response = await fetch('/get_cells_in_lassos', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(
                    {
                        datasource: datasource,
                        list_lassos: list_lassos,
                    }
                )
            });
            let cells = await response.json();
            return cells;
        } catch (e) {
            console.log("Error Getting Cells in Lassos", e);
        }
    }

}
