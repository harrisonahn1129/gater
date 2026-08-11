const d3 = require('d3');
const sinon = require('sinon');
const Stream = require('stream');
const mockttp = require("mockttp");
var configData = require('../data/config.json');
var metaData = require('../data/get_ome_metadata.json');
var channelForm = require('../data/formData/download_channels.json');
var rangeForm = require('../data/formData/gated_channel_ranges.json');
var encodingForm = require('../data/formData/gated_cell_encodings.json');
var databaseData = require('../data/get_database_description.json');
var channelGMM0 = require('../data/get_channel_gmm/Hoechst0.json');
var gatingGMM0 = require('../data/get_gating_gmm/Hoechst0.json');
var shortData = require('../data/get_channel_names/short.json');

// These are set in test/fixtures/context.html
declare var __GLOBAL__RESET__FUNCTION__: () => void;
declare var __GLOBAL__INITIALIZATION__FUNCTION__: () => void;
// Types
type StreamBuffer = (stream: ReadableStream<any>) => Promise<Uint8Array>
type LoadBuffer = (url: string) => Promise<Uint8Array>
type LoadText = (url: string) => Promise<string>
declare var __minervaAnalysis: MinervaAnalysis;
type TopColors = {
  colors: string[],
  counts: number[]
}

// Types defined by application
const OpenSeadragon = require("openseadragon");
type Viewer = typeof OpenSeadragon.Viewer;
type World = typeof OpenSeadragon.World;
interface ViewerManager {
  viewer: Viewer;
}
interface SeaDragonViewer {
  viewerManagers: ViewerManager[];
}
interface CsvGatingList {
  seaDragonViewer: SeaDragonViewer;
  download_panel_visible: boolean;
}
interface Rainbow {
  show(x: number, y: number): void;
  set(hsl: any): void;
}
interface ChannelList {
  colorTransferHandle: any;
  rainbow: Rainbow; 
}
interface MinervaAnalysis {
  csv_gatingList: CsvGatingList;
  channelList: ChannelList;
}

const MOCK_PORT = 8765;
const KARMA_DATASOURCE = "karma-test";
const CLEAR_PREFIX = "crop-mask-crc01";
const LOGO_PREFIX = "crop-crc01";
const CHANNEL_ZERO = "Hoechst0";
const KARMA_QUERY = {
  datasource: KARMA_DATASOURCE,
}
const toUrlCsv = (...items) => items.join(",");
const CELL_ID_CENTER = toUrlCsv("CellID", "X_centroid", "Y_centroid");

const toHeaders = (bytes: number, meaning: string) => {
  const extraHeaders = {
    'png': {
      'Content-Type': 'image/PNG',
    },
    'gzip': {
      'Content-Encoding': 'gzip',
      'Content-Type': 'application/octet-stream',
    }
  }[meaning]
  return { 
    ...extraHeaders,
    'Content-Length': bytes
  }
}

const expect = chai.expect;
var mockServer; 
var allCellBuffer;
var c0CellBuffer;
var clearBuffer;
var logoBuffer;


before(async () => {
  mockServer = mockttp.getLocal();
  allCellBuffer = Buffer.from(await loadBuffer('/data/cell_id_center.bin.gz'));
  c0CellBuffer = Buffer.from(await loadBuffer('/data/cell_hoechst.bin.gz'));
  clearBuffer = Buffer.from(await loadBuffer('/data/1024x1024_clear.png'));
  logoBuffer = Buffer.from(await loadBuffer('/data/1024x1024_logo.png'));
  await __GLOBAL__INITIALIZATION__FUNCTION__();
  fixture.setBase('html')
});


function streamToAsyncIterator(readable : ReadableStream) : AsyncIterableIterator<Uint8Array> {
  const reader = readable.getReader();
  return {
    next(){
      return reader.read();
    },
    return(){
      return reader.releaseLock();
    },
    [Symbol.asyncIterator](){
      return this;
    }
  } as AsyncIterableIterator<Uint8Array>;
}

const streamBuffer: StreamBuffer = async (readable) => {
  let chunks: number[] = [];
  const iterable = streamToAsyncIterator(readable);
  for await (const chunk of iterable) {
    for (const value of chunk) {
      chunks.push(value);
    }
  }
  return Uint8Array.from(chunks);
}

const loadBuffer: LoadBuffer = async (url) => {
  const stream = (await fetch(url)).body;
  return await streamBuffer(stream);
}

const loadText: LoadText = async (url) => {
  return (await fetch(url)).text();
}

const getProperty = (scope: any, k: string | symbol) => {
  const v = scope[k];
  return typeof v === "function" ? v.bind(scope) : v;
};

// Drives one of the gating download-panel save buttons and returns the JSON
// body it POSTed to OMERO (or null if it never posted). The CSVs now go to the
// OMERO image over fetch, so this stubs fetch rather than intercepting a form
// submit, and stubs alert because the success message would otherwise block.
const saveGatingCsv = async (buttonId, inputId, name) => {
  const panel = document.getElementById('gating_download_panel');
  const { csv_gatingList } = __minervaAnalysis;
  csv_gatingList.download_panel_visible = true;
  (panel as HTMLElement).style.visibility = 'visible';
  (document.getElementById(inputId) as HTMLInputElement).value = name;

  let sent: any = null;
  const fetchStub = sinon.stub(window, 'fetch').callsFake((url, init) => {
    const u = String(url);
    if (u.indexOf('/list_omero_gating_csvs') !== -1) {
      // Nothing saved yet, so no overwrite confirm is raised.
      return Promise.resolve(new Response(JSON.stringify({csvs: []}),
        {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    if (u.indexOf('/save_gating_csv_to_omero') !== -1) {
      sent = JSON.parse(init.body);
      return Promise.resolve(new Response(
        JSON.stringify({success: true, rows: 0, name: sent.csv_name}),
        {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    return (fetchStub as any).wrappedMethod.call(window, url, init);
  });
  sinon.stub(window, 'alert');
  document.getElementById(buttonId).dispatchEvent(new Event('click'));
  await sleeper(1);
  sinon.restore();
  return sent;
}

beforeEach(async () => {

  await mockServer.start(MOCK_PORT);
  // Load config endpoint
  const configString = JSON.stringify(configData);
  const configMock = mockServer.forGet("/config");
  // Load channel names endpoint
  const _shortMock = mockServer.forGet("/get_channel_names");
  const shortMock = _shortMock.withQuery({
    ...KARMA_QUERY,
    shortNames: "true"
  })
  // Load ome metadata endpoint
  const _metaMock = mockServer.forGet("/get_ome_metadata");
  const metaMock = _metaMock.withQuery(KARMA_QUERY)
  // Load ome database description endpoint
  const _databaseMock = mockServer.forGet("/get_database_description");
  const databaseMock = _databaseMock.withQuery(KARMA_QUERY)
  // Load clear image endpoints
  const prefix = `/generated/data/${KARMA_DATASOURCE}`;
  const clearRegExp = `^${prefix}/${CLEAR_PREFIX}-.*`
  const clearHeaders = toHeaders(clearBuffer.length, 'png');
  const clearMock = mockServer.forGet(new RegExp(clearRegExp));
  // Load logo image endpoints
  const logoRegExp = `${prefix}/${LOGO_PREFIX}-.*`
  const logoHeaders = toHeaders(logoBuffer.length, 'png');
  const logoMock = mockServer.forGet(new RegExp(logoRegExp));
  // Load cell index endpoint
  const allCellHeaders = toHeaders(allCellBuffer.length, 'gzip');
  const _allCellMock = mockServer.forGet("/get_all_cells/integer/");
  const allCellMock = _allCellMock.withQuery({
    ...KARMA_QUERY,
    start_keys: CELL_ID_CENTER 
  });
  // Load cell gating keys endpoint
  const c0CellHeaders = toHeaders(c0CellBuffer.length, 'gzip');
  const _c0CellMock = mockServer.forGet("/get_all_cells/float/");
  const c0CellMock = _c0CellMock.withQuery({
    ...KARMA_QUERY,
    start_keys: CHANNEL_ZERO 
  });
  // Load database init endpoint
  const _initMock = mockServer.forGet("/init_database");
  const initMock = _initMock.withQuery(KARMA_QUERY)
  // Load channel data endpoints
  const _channelGMM0Mock = mockServer.forGet("/get_channel_gmm");
  const channelGMM0Mock = _channelGMM0Mock.withQuery({
    ...KARMA_QUERY,
    channel: CHANNEL_ZERO 
  });
  // Load gating data endpoints
  const _gatingGMM0Mock = mockServer.forGet("/get_gating_gmm");
  const gatingGMM0Mock = _gatingGMM0Mock.withQuery({
    ...KARMA_QUERY,
    channel: CHANNEL_ZERO 
  });
  // Save the channel CSV (now an attachment on the OMERO image, not a download)
  const channelsMock = mockServer.forPost("/save_channels_csv_to_omero");
  const gatingMock = mockServer.forPost("/save_gating_csv_to_omero");
  // Await all endpoints
  await Promise.all([
    configMock.thenJson(200, configData),
    shortMock.thenJson(200, shortData),
    metaMock.thenJson(200, metaData),
    databaseMock.thenJson(200, databaseData),
    channelGMM0Mock.thenJson(200, channelGMM0),
    gatingGMM0Mock.thenJson(200, gatingGMM0),
    initMock.thenJson(200, {success: true}),
    clearMock.thenReply(200, clearBuffer, clearHeaders),
    logoMock.thenReply(200, logoBuffer, logoHeaders),
    c0CellMock.thenReply(200, c0CellBuffer, c0CellHeaders),
    allCellMock.thenReply(200, allCellBuffer, allCellHeaders),
    channelsMock.thenCallback(() => ''),
    gatingMock.thenCallback(() => '')
])
  // Run the main entrypoint
  this.result = fixture.load('main.html');
  __GLOBAL__RESET__FUNCTION__();
});

afterEach(function(){
  fixture.cleanup();
  const els = [
    ...document.getElementsByClassName("picker-container")
  ]
  for (const el of els) {
    el.remove();
  }
  return mockServer.stop();
  // Restore spies
  sinon.restore();
});

const sleeper = async (sec: number) => {
  return await new Promise(r => setTimeout(r, sec * 1024));
}

const setChannelColorZero = async (color: string) => {
  const cList = document.getElementById("channel_list");
  const cEl = cList.getElementsByClassName("list-group-item")[0];
  const rect = cEl.getElementsByTagName("rect")[0];
  const { x, y } = rect.getBoundingClientRect();
  const { channelList } = __minervaAnalysis;
  channelList.colorTransferHandle = d3.select(rect);
  const { rainbow } = channelList;
  const hsl = d3.hsl(color);
  rainbow.show(x, y);
  await sleeper(0.25);
  rainbow.set(hsl);
  await sleeper(0.25);
  const pick = document.getElementsByClassName("picker-container")[0];
  const checkmark = pick.getElementsByClassName("save")[0];
  $(checkmark).click();
  await sleeper(0.25);
}

const clickChannelZero = async (t: number) => {
  const cList = document.getElementById("channel_list");
  const cEl = cList.getElementsByClassName("list-group-item")[0];
  $(cEl).click();
  await sleeper(t);
}

const clickMaskZero = async (t: number) => {
  const cList = document.getElementById("csv_gating_list");
  const cEl = cList.getElementsByClassName("list-group-item")[0];
  $(cEl).click();
  await sleeper(t);
}

const toWorld = (): World => {
  const { csv_gatingList } = __minervaAnalysis;
  const { seaDragonViewer } = csv_gatingList;
  const { viewerManagers } = seaDragonViewer;
  return viewerManagers[0].viewer.world;
}

const toImageData = (): ImageData => {
  const rootEl = document.getElementById("openseadragon");
  const el = document.getElementsByTagName("canvas")[0];
  const context = el.getContext("2d");
  const width = context.canvas.clientWidth;
  const height = context.canvas.clientHeight;
  return context.getImageData(0, 0, width, height);
}

const toHexColor = (r, g, b) => {
  return [r, g, b].map(n => {
    return n.toString(16).padStart(2, 0);
  }).join('')
}

const toTopColors = (): TopColors => {
  const { data } = toImageData();
  const hist = new Map();
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const hex = toHexColor(r, g, b);
    const freq = hist.has(hex) ? hist.get(hex) : 0;
    hist.set(hex, freq + 1);
  }
  const sorted = [...hist].sort((a, b) => b[1] - a[1])
  const colors = sorted.map(v => v[0]);
  const counts = sorted.map(v => v[1]);
  return {
    colors,
    counts
  }
}

describe('Load', function () {
  describe('Ensure basic loading', function () {
    it('must load a channel', async function () {
      await sleeper(1);
      const world = toWorld();
      const itemCountBefore = world.getItemCount();
      await clickChannelZero(0.5);
      const itemCountAfter = world.getItemCount();
      expect(itemCountBefore).to.equal(1);
      expect(itemCountAfter).to.equal(2);
    })
  })
  describe('Ensure visual rendering', function () {
    it('must load a mask', async function () {
      await sleeper(1);
      const world = toWorld();
      await clickChannelZero(1);
      await clickMaskZero(1);
      // Disable outline mode
      await $('#gating_controls_outlines').click();
      await sleeper(1);
      // Ensure expected white/black ratio
      (({ colors, counts }: TopColors) => {
        const white_ratio = counts[0] / (counts[0] + counts[1]);
        white_ratio.should.be.approximately(0.5058, 0.01);
        expect(colors[0]).to.equal('ffffff');
        expect(colors[1]).to.equal('000000');
      })(toTopColors());
      // Set channel color
      await setChannelColorZero('#0000ff');
      // Enable outline mode
      await $('#gating_controls_outlines').click();
      await sleeper(1);
      // Ensure expected black/blue ratio
      (({ colors, counts }: TopColors) => {
        const blue_ratio = counts[0] / (counts[0] + counts[1]);
        blue_ratio.should.be.approximately(0.5932, 0.01);
        expect(colors[0]).to.equal('000000');
        expect(colors[1]).to.equal('000093');
      })(toTopColors());
      await sleeper(1);
    })
  })
  describe('Ensure channel CSV save', function () {
    it('must post the channel list to OMERO as CSV', async function () {
      await sleeper(1);
      // The channel CSV is now written to the OMERO image over fetch; it used
      // to be a hidden-form POST that downloaded a file. Same payload as the
      // old form (minus `filename`), but as real JSON rather than the form's
      // stringified values -- so compare against the parsed fixture.
      let sent: any = null;
      const fetchStub = sinon.stub(window, 'fetch').callsFake((url, init) => {
        const u = String(url);
        if (u.indexOf('/list_omero_channel_csvs') !== -1) {
          // No CSVs yet, so the name dialog shows no overwrite warning.
          return Promise.resolve(new Response(JSON.stringify({csvs: []}),
            {status: 200, headers: {'Content-Type': 'application/json'}}));
        }
        if (u.indexOf('/save_channels_csv_to_omero') !== -1) {
          sent = JSON.parse(init.body);
          return Promise.resolve(new Response(
            JSON.stringify({success: true, rows: 0, name: sent.csv_name}),
            {status: 200, headers: {'Content-Type': 'application/json'}}));
        }
        return (fetchStub as any).wrappedMethod.call(window, url, init);
      });
      const alertStub = sinon.stub(window, 'alert');   // success alert would block
      const cIcon = document.getElementById("channels_download_icon");
      cIcon.dispatchEvent(new Event('click'));
      // Saving now goes through a "name the file" dialog; accept the default.
      await sleeper(1);
      const saveBtn = document.querySelector('.gater-picker-save') as HTMLButtonElement;
      expect(saveBtn).to.not.equal(null);
      saveBtn.click();
      await sleeper(1);
      expect(sent).to.not.equal(null);
      expect(sent.csv_name).to.equal(`${channelForm.datasource}_channels.csv`);
      expect(sent.overwrite).to.equal(false);
      expect(sent.datasource).to.equal(channelForm.datasource);
      ['active_channels', 'list_channels', 'map_channels',
       'list_colors', 'list_ranges'].forEach((key) => {
        expect(sent[key]).to.deep.equal(JSON.parse(channelForm[key]));
      });
      expect(alertStub.called).to.equal(true);
      sinon.restore();
      await sleeper(3);
    })
  })
  describe('Ensure gating range CSV save', function () {
    it('must post gated channel ranges to OMERO', async function () {
      await sleeper(1);
      const sent = await saveGatingCsv(
        'download_gated_channel_ranges', 'download_input1', 'ranges test.csv');
      expect(sent).to.not.equal(null);
      expect(sent.fullCsv).to.equal(false);
      // Spaces are replaced so the stored name matches what OMERO displays.
      expect(sent.csv_name).to.equal('ranges_test.csv');
      expect(sent.overwrite).to.equal(false);
      expect(sent.datasource).to.equal(rangeForm.datasource);
      expect(sent.encoding).to.equal(rangeForm.encoding);
      expect(sent.filter).to.deep.equal(JSON.parse(rangeForm.filter));
      expect(sent.channels).to.deep.equal(JSON.parse(rangeForm.channels));
      await sleeper(3);
    })
  })
  describe('Ensure download encodings', function () {
    it('must post gated cell encodings to OMERO', async function () {
      await sleeper(1);
      const sent = await saveGatingCsv(
        'download_gated_cell_encodings', 'download_input2', 'encodings.csv');
      expect(sent).to.not.equal(null);
      expect(sent.fullCsv).to.equal(true);
      expect(sent.csv_name).to.equal('encodings.csv');
      expect(sent.datasource).to.equal(encodingForm.datasource);
      expect(sent.encoding).to.equal(encodingForm.encoding);
      expect(sent.filter).to.deep.equal(JSON.parse(encodingForm.filter));
      expect(sent.channels).to.deep.equal(JSON.parse(encodingForm.channels));
      await sleeper(3);
    })
  })
})
