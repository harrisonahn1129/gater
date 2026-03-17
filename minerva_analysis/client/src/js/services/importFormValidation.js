

// Example starter JavaScript for disabling form submissions if there are invalid fields
(function () {
    'use strict'

    // Fetch all the forms we want to apply custom Bootstrap validation styles to
    var forms = document.querySelectorAll('.needs-validation')
    // Loop over them and prevent submission
    Array.prototype.slice.call(forms)
        .forEach(function (form) {
            form.addEventListener('submit', function (event) {
                if (!form.checkValidity()) {
                    event.preventDefault()
                    event.stopPropagation()
                } else {
                    onupload();
                }
                form.classList.add('was-validated')
            }, true)
        })
})()

//add listener
d3.select("#import_type").on("change", update);

function update() {
    if (d3.select("#import_type").property("checked")) {
        console.log('is checked');
        d3.select('#mcmicro_form').style("display", 'inline');
        d3.select('#custom_form').style("display", 'none');
    } else {
        d3.select('#mcmicro_form').style("display", 'none');
        d3.select('#custom_form').style("display", 'inline');
    }
}

//check if path exists and contains .ome.tif and .csv files
async function checkMCOutputFolder(caller) {
    let inputField = d3.select('#' + caller.id);
    let path = inputField.property("value");

    try {
        let response = await fetch('/check_mc_output_folder', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({path: path})
        });
        let result = await response.json();

        if (!result.path_exists) {
            inputField.attr("class", "form-control is-invalid");
            inputField.node().setCustomValidity('Path does not exist.');
            d3.select("#mcmicro_path_validation_text").html('Please provide a valid path.');
        } else if (!result.has_ome_tif && !result.has_csv) {
            inputField.attr("class", "form-control is-invalid");
            inputField.node().setCustomValidity('Invalid');
            d3.select("#mcmicro_path_validation_text").html('No .ome.tif or .csv files found in this directory.');
        } else if (!result.has_ome_tif) {
            inputField.attr("class", "form-control is-invalid");
            inputField.node().setCustomValidity('Invalid');
            d3.select("#mcmicro_path_validation_text").html('No .ome.tif file found in this directory.');
        } else if (!result.has_csv) {
            inputField.attr("class", "form-control is-invalid");
            inputField.node().setCustomValidity('Invalid');
            d3.select("#mcmicro_path_validation_text").html('No .csv file found in this directory.');
        } else {
            inputField.attr("class", "form-control is-valid");
            inputField.node().setCustomValidity('');
        }
    } catch (e) {
        console.log("Error checking MCMICRO output folder", e);
    }
}

//check the existence of a CSV file (MCMICRO specific)
async function checkCSVFileExistence(caller) {
    const self = this;

    //get folder path from the input text field
    let maskSelectionField = d3.select('#' + caller.id);
    let mask = maskSelectionField.property("value");

    //get selected mask type from the selection field
    let pathInputField = d3.select('#' + 'mcmicro_output_folder');
    let path = pathInputField.property("value");

    try {
        //check if corresponsindg csv file exists
        let response = await fetch('/check_mc_csv_file_existence', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(
                {
                    path: path,
                    mask: mask
                }
            )
        });
        let response_data = await response.json();
        if (response_data == true) {
            maskSelectionField.attr("class", "form-control is-valid");
            maskSelectionField.node().setCustomValidity('');
        } else {
            d3.select("#" + 'mcmicro_mask_validation_text').html('No corresponding csv file found.')
            maskSelectionField.attr("class", "form-control is-invalid");
            maskSelectionField.node().setCustomValidity('Invalid');
        }
        return response_data;
    } catch (e) {
        console.log("Error While Checking for CSV File Existence", e);
    }
}

//check if path exists (mcmicro naming specific)
async function checkFileExistence(caller) {
    const self = this;
    let inputField = d3.select('#' + caller.id);
    //get segmentation folder path from the input text field
    let path = inputField.property("value");

    try {
        //get available segmentation masks in mcmicro directory from server
        let response = await fetch('/check_file_existence', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(
                {
                    path: path,
                }
            )
        });
        let response_data = await response.json();
        if (response_data == true) {
            inputField.attr("class", "form-control is-valid");
            inputField.node().setCustomValidity('');
        } else {
            inputField.attr("class", "form-control is-invalid");
            inputField.node().setCustomValidity('Invalid');
        }
        return response_data;
    } catch (e) {
        console.log("Error Getting Segmentation File List", e);
    }
}


//check if dataset already exists
//check if path exists (mcmicro naming specific)
async function checkDatasetExistence(caller) {
    const self = this;
    let inputField = d3.select('#' + caller.id);
    //get segmentation folder path from the input text field
    let datasetName = inputField.property("value");

    try {
        //get available segmentation masks in mcmicro directory from server
        let response = await fetch('/dataset_existence', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(
                {
                    dataset_name: datasetName,
                }
            )
        });
        let response_data = await response.json();
        if (response_data == false) {
            inputField.attr("class", "form-control is-valid");
            inputField.node().setCustomValidity('');
        } else {
            inputField.attr("class", "form-control is-invalid");
            inputField.node().setCustomValidity('Dataset name already exists. Choose a different name.');
        }
        // inputField.node().reportValidity();
        return response_data;
    } catch (e) {
        console.log("Error Getting Segmentation File List", e);
    }
}

//get a list of available files in a folder (mcmicro naming specific)
async function fillCSVFileList() {
    const self = this;

    //get segmentation folder path from the input text field
    let path = d3.select('#mcmicro_output_folder').property("value");

    //remove old selection options as soon as path changes


    try {
        //get available segmentation masks in mcmicro directory from server
        let response = await fetch('/get_mc_csv_file_list', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(
                {
                    path: path,
                }
            )
        });
        let response_data = await response.json();
        var select_field = document.getElementById("mcmicro_masks");
        select_field.innerHTML = "";
        //fill select form field with new options
        response_data.forEach(function (option_value) {
            var option = document.createElement("option");
            option.text = option_value;
            option.value = option_value;
            select_field.add(option);
        })

        //return the filled field
        return response_data;
    } catch (e) {
        console.log("Error Getting Segmentation File List", e);
    }
}

//get a list of available files in a folder (mcmicro naming specific)
async function fillImgFileList() {
    const self = this;

    //get segmentation folder path from the input text field
    let path = d3.select('#mcmicro_output_folder').property("value");




    try {
        //get available segmentation masks in mcmicro directory from server
        let response = await fetch('/get_mc_segmentation_file_list', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(
                {
                    path: path,
                }
            )
        });
        let response_data = await response.json();
        //remove old selection options as soon as path changes
        var select_field = document.getElementById("mcmicro_images");
        select_field.innerHTML = "";
        //fill select form field with new options
        response_data.forEach(function (option_value) {
            var option = document.createElement("option");
            option.text = option_value;
            option.value = option_value;
            select_field.add(option);
        })

        //return the filled field
        return response_data;
    } catch (e) {
        console.log("Error Getting Channel File List", e);
    }
}

//get a list of available files in a folder (mcmicro naming specific)
async function fillSegFileList() {
    const self = this;

    //get segmentation folder path from the input text field
    let path = d3.select('#mcmicro_output_folder').property("value");

    //remove old selection options as soon as path changes


    try {
        //get available segmentation masks in mcmicro directory from server
        let response = await fetch('/get_mc_segmentation_file_list', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(
                {
                    path: path,
                }
            )
        });
        let response_data = await response.json();
        var select_field = document.getElementById("mcmicro_seg");
        select_field.innerHTML = "";
        //fill select form field with new options
        response_data.forEach(function (option_value) {
            var option = document.createElement("option");
            option.text = option_value;
            option.value = option_value;
            select_field.add(option);
        })

        //return the filled field
        return response_data;
    } catch (e) {
        console.log("Error Getting Segmentation File List", e);
    }
}


let uploadPercentage = 0;
let ajaxForm = $('form').ajaxForm({
    uploadProgress: function (event, position, total, percentComplete) {
        uploadPercentage = percentComplete;
    },
    success: function (res) {
        document.write(res);
    }
});

function displayPercentage(totalPercentage, currentTask) {
    if (totalPercentage == 0) {
        $('.progress-bar-label').css('display', 'none');
    } else {
        $('.progress-bar-label').css('display', 'block');
    }
    $('.progress-bar').css('width', totalPercentage + '%').attr('aria-valuenow', totalPercentage);
    $("#progress-bar-percentage").text(totalPercentage + '%');
    $("#progress-bar-current-task").text(currentTask);
}

let consecutiveErrors = 0;
// $('#upload_button').on('click', onupload());
// $('#upload_button_mcmicro').on('click', onupload());

function onupload() {
    uploadPercentage = 0;
    // Hide whatever header exists
    displayHeader('', false, true);
    var source = new EventSource("/progress");
    source.onmessage = function (event) {
        let data = JSON.parse(event.data);
        consecutiveErrors = 0;

        if (data.percentage < 0) {
            console.log("Error, Terminating");
            displayPercentage(0, '');
            if (data.currentTask) {
                displayHeader(data.currentTask, true)
            }
            source.close();
            return;
        }
        let combinedPercentage = (data.percentage + (uploadPercentage || 0)) / 2;
        console.log("Parsed Data:", data, "combinedPercentage", combinedPercentage, "UL P", uploadPercentage);
        displayPercentage(combinedPercentage, data.currentTask);
        if (combinedPercentage >= 100) {
            displayHeader("Upload and Conversion Complete", false);
            source.close();
        }
    }
    source.onerror = function (event) {
        consecutiveErrors += 1;
        if (consecutiveErrors > 10) {
            console.log("Error, Terminating");
            displayPercentage(0, '');
            displayHeader("Error", true);
            source.close();
        }
    }
}

function displayHeader(text, isError, hide = false) {
    if (hide) {
        $('#upload-message').empty()
    } else {
        if (isError) {
            $('#upload-message').empty()
            $('#upload-message').append("<span class='error'>" + text + "</span>");
        } else {
            $('#upload-message').empty()
            $('#upload-message').append("<span class='success'>" + text + "</span>");
        }
    }
}