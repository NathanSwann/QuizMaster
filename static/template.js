function render_template(tempalate_id, params) {
    var template = document.getElementById(tempalate_id).innerHTML.toString();
    template = template.replace("<!----","")
    template = template.replace("---->","")
    const keys = Object.keys(params);
    const keyVals = Object.values(params);
    return new Function(...keys, `return \`${template}\``)(...keyVals);
}